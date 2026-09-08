#!/usr/bin/env python
"""
Lint de archivos .bat: dos chequeos independientes.

1. `echo` con parentesis literales DENTRO de un bloque. En cmd, dentro de un
   bloque `if (...)` / `for (...)`, un parentesis literal en una linea `echo`
   cierra el bloque antes de tiempo y rompe el script con "no se esperaba .
   en este momento" (bug #5 del go-live de Royal Plast). Los `echo` a nivel 0
   pueden tener parentesis sin problema.

2. Bytes de control invisibles (BEL, BS, VT, FF) en el archivo. Encontrado en
   produccion (visita a Royal Plast, 2026-08-24): una ruta `\venv` escrita a
   mano en algun editor/generador se convirtio en `\x0B` (vertical tab)
   literal -- alguien tecleo la secuencia de escape `\v` de Python/C y quedo
   interpretada. El resultado: `call "...\x0Benv\Scripts\python.exe"` apunta
   a una ruta que no existe, el `call` falla sin chequeo de errorlevel, y el
   script sigue de largo como si nada. Ninguno de estos 4 bytes tiene uso
   legitimo en un .bat.

Estrategia (1): lleva la profundidad de bloque (un trailing `(` abre, un
leading `)` cierra, `) else (` cierra y abre). Si en profundidad > 0 aparece
un `echo` (que no es la linea estructural) con `(` o `)`, lo reporta.

Uso:  python lint_bat.py <carpeta>     (default: deploy)
Exit: 0 si no hay hallazgos, 1 si hay.
"""
import glob
import os
import sys

# BEL, BS, VT, FF. Los mas comunes de ver "colados" porque coinciden con una
# secuencia de escape de otro lenguaje (\a \b \v \f) que alguien tecleo
# pensando en una ruta, no en un caracter de control.
_BYTES_CONTROL_SOSPECHOSOS = {
    0x07: r'\a (BEL)',
    0x08: r'\b (BS)',
    0x0B: r'\v (VT)',
    0x0C: r'\f (FF)',
}


def lint_archivo(path):
    hallazgos = []
    depth = 0
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip('\r\n').strip()
            if not line:
                continue
            es_apertura = line.endswith('(')
            es_cierre = line.startswith(')')
            es_echo = line.lower().startswith('echo')

            # echo de cuerpo (no estructural) dentro de un bloque con parentesis
            if depth > 0 and es_echo and not es_apertura and not es_cierre:
                if '(' in line or ')' in line:
                    hallazgos.append((n, line))

            # actualizar profundidad (cerrar antes de abrir cubre `) else (`)
            if es_cierre:
                depth = max(0, depth - 1)
            if es_apertura:
                depth += 1
    return hallazgos


def lint_bytes_control(path):
    """
    Reporta (numero_de_linea, nombre_del_byte, contexto) por cada byte de
    control sospechoso. Se trabaja en BYTES, no en texto decodificado: un
    control char es valido UTF-8 (es ASCII), asi que decodificar y
    re-codificar no lo habria delatado ni corregido.
    """
    hallazgos = []
    with open(path, 'rb') as f:
        data = f.read()

    linea = 1
    for i, byte in enumerate(data):
        if byte == 0x0A:  # \n
            linea += 1
            continue
        if byte in _BYTES_CONTROL_SOSPECHOSOS:
            inicio = max(0, i - 20)
            contexto = data[inicio:i + 20]
            contexto_legible = contexto.decode('utf-8', errors='replace').replace('\r', '').replace('\n', ' ')
            hallazgos.append((linea, _BYTES_CONTROL_SOSPECHOSOS[byte], contexto_legible))
    return hallazgos


def main():
    carpeta = sys.argv[1] if len(sys.argv) > 1 else 'deploy'
    total = 0
    for path in sorted(glob.glob(os.path.join(carpeta, '*.bat'))):
        for n, texto in lint_archivo(path):
            total += 1
            print(f'  [LINT] {os.path.basename(path)}:{n}: echo con parentesis dentro de '
                  f'bloque -> {texto}')
        for n, nombre_byte, contexto in lint_bytes_control(path):
            total += 1
            print(f'  [LINT] {os.path.basename(path)}:{n}: byte de control {nombre_byte} '
                  f'colado en el archivo -> ...{contexto}...')
    if total:
        print(f'[ERROR] Lint .bat: {total} hallazgo(s). Quita los parentesis de esos echo '
              f'o sacalos del bloque if(...); revisa los bytes de control con un editor '
              f'hexadecimal y reescribe la linea a mano.')
        return 1
    print('[OK] Lint .bat: sin echo con parentesis dentro de bloques ni bytes de control.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
