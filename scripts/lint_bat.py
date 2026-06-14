#!/usr/bin/env python
"""
Lint de archivos .bat: detecta `echo` con parentesis literales DENTRO de un bloque.

Contexto: en cmd, dentro de un bloque `if (...)` / `for (...)`, un parentesis literal
en una linea `echo` cierra el bloque antes de tiempo y rompe el script con
"no se esperaba . en este momento" (bug #5 del go-live de Royal Plast). Los `echo` a
nivel 0 pueden tener parentesis sin problema.

Estrategia: lleva la profundidad de bloque (un trailing `(` abre, un leading `)` cierra,
`) else (` cierra y abre). Si en profundidad > 0 aparece un `echo` (que no es la linea
estructural) con `(` o `)`, lo reporta.

Uso:  python lint_bat.py <carpeta>     (default: deploy)
Exit: 0 si no hay hallazgos, 1 si hay.
"""
import glob
import os
import sys


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


def main():
    carpeta = sys.argv[1] if len(sys.argv) > 1 else 'deploy'
    total = 0
    for path in sorted(glob.glob(os.path.join(carpeta, '*.bat'))):
        for n, texto in lint_archivo(path):
            total += 1
            print(f'  [LINT] {os.path.basename(path)}:{n}: echo con parentesis dentro de '
                  f'bloque -> {texto}')
    if total:
        print(f'[ERROR] Lint .bat: {total} hallazgo(s). Quita los parentesis de esos echo '
              f'o sacalos del bloque if(...).')
        return 1
    print('[OK] Lint .bat: sin echo con parentesis dentro de bloques.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
