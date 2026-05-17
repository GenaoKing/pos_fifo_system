"""
test_cloud_latency.py — Pruebas de latencia para BD cloud
==========================================================
Uso:
    call deploy\env_azure_pg_local.bat
    python deploy\test_cloud_latency.py

    call deploy\env_azure_sql_local.bat
    python deploy\test_cloud_latency.py

Mide latencia real desde Santo Domingo hacia la BD cloud configurada
en las variables de entorno DJANGO_SETTINGS_MODULE.

Genera un reporte en consola y guarda resultados en:
    docs/latency_results_{entorno}_{fecha}.json
"""

import os
import sys
import json
import time
import statistics
from datetime import datetime
from pathlib import Path

# Configurar Django
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

settings_module = os.environ.get('DJANGO_SETTINGS_MODULE', 'config.settings_azure_pg')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', settings_module)

import django
django.setup()

from django.db import connection, connections
from django.conf import settings


# ============================================================================
# CONFIGURACION DE PRUEBAS
# ============================================================================
NUM_ITERATIONS = 20         # Repeticiones por prueba
WARMUP_ITERATIONS = 3       # Conexiones de calentamiento (descartadas)

# Queries que simulan operaciones reales del POS
TEST_QUERIES = {
    'ping': {
        'sql': 'SELECT 1',
        'descripcion': 'Ping basico (latencia pura de red)',
    },
    'count_tables': {
        'sql': "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'" 
               if 'postgresql' in settings.DATABASES['default'].get('ENGINE', '') 
               else "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES",
        'descripcion': 'Contar tablas (metadata query)',
    },
    'current_time': {
        'sql': 'SELECT NOW()' 
               if 'postgresql' in settings.DATABASES['default'].get('ENGINE', '') 
               else 'SELECT GETDATE()',
        'descripcion': 'Hora del servidor (funcion simple)',
    },
}


def measure_query(cursor, sql, iterations=NUM_ITERATIONS):
    """Ejecuta una query N veces y retorna estadisticas de latencia en ms."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        cursor.execute(sql)
        cursor.fetchone()
        elapsed = (time.perf_counter() - start) * 1000  # ms
        times.append(elapsed)
    
    return {
        'min_ms': round(min(times), 2),
        'max_ms': round(max(times), 2),
        'avg_ms': round(statistics.mean(times), 2),
        'median_ms': round(statistics.median(times), 2),
        'p95_ms': round(sorted(times)[int(len(times) * 0.95)], 2),
        'stdev_ms': round(statistics.stdev(times), 2) if len(times) > 1 else 0,
        'samples': len(times),
        'all_ms': [round(t, 2) for t in times],
    }


def measure_connection_time():
    """Mide cuanto tarda establecer una conexion nueva (incluye SSL handshake)."""
    times = []
    for _ in range(5):
        # Forzar nueva conexion
        connections['default'].close()
        
        start = time.perf_counter()
        connections['default'].ensure_connection()
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    
    return {
        'min_ms': round(min(times), 2),
        'max_ms': round(max(times), 2),
        'avg_ms': round(statistics.mean(times), 2),
        'samples': len(times),
    }


def measure_django_orm():
    """Mide latencia de operaciones Django ORM tipicas del POS."""
    from django.contrib.contenttypes.models import ContentType
    
    results = {}
    
    # ORM: Query simple (equivalente a lo que hace el POS al buscar productos)
    times = []
    for _ in range(NUM_ITERATIONS):
        start = time.perf_counter()
        list(ContentType.objects.all()[:5])
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    
    results['orm_select_limit5'] = {
        'descripcion': 'ORM SELECT con LIMIT 5 (simula busqueda producto)',
        'avg_ms': round(statistics.mean(times), 2),
        'median_ms': round(statistics.median(times), 2),
        'p95_ms': round(sorted(times)[int(len(times) * 0.95)], 2),
    }
    
    # ORM: Count
    times = []
    for _ in range(NUM_ITERATIONS):
        start = time.perf_counter()
        ContentType.objects.count()
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    
    results['orm_count'] = {
        'descripcion': 'ORM COUNT (simula verificacion stock)',
        'avg_ms': round(statistics.mean(times), 2),
        'median_ms': round(statistics.median(times), 2),
        'p95_ms': round(sorted(times)[int(len(times) * 0.95)], 2),
    }
    
    return results


def evaluate_pos_impact(ping_avg, orm_avg):
    """Evalua el impacto en UX del POS segun la latencia medida."""
    print("\n" + "=" * 60)
    print("EVALUACION DE IMPACTO EN UX DEL POS")
    print("=" * 60)
    
    # Una venta tipica ejecuta ~5-8 queries (buscar producto, verificar stock,
    # crear venta, crear detalles, consumir FIFO, crear pago, actualizar caja)
    estimated_sale_ms = orm_avg * 7  # 7 queries promedio por venta
    
    print(f"\n  Latencia ping promedio:           {ping_avg:.0f} ms")
    print(f"  Latencia ORM promedio:            {orm_avg:.0f} ms")
    print(f"  Venta estimada (7 queries):       {estimated_sale_ms:.0f} ms")
    
    if ping_avg < 50:
        print(f"\n  [EXCELENTE] Latencia < 50ms — UX del POS no se vera afectado")
    elif ping_avg < 100:
        print(f"\n  [BUENO] Latencia 50-100ms — UX aceptable, busquedas algo mas lentas")
    elif ping_avg < 200:
        print(f"\n  [ACEPTABLE] Latencia 100-200ms — Perceptible en busquedas, ventas OK")
    elif ping_avg < 500:
        print(f"\n  [LENTO] Latencia 200-500ms — POS se sentira lento, considerar cache")
    else:
        print(f"\n  [INACEPTABLE] Latencia > 500ms — No viable para POS en tiempo real")
    
    if estimated_sale_ms < 500:
        print(f"  Venta: Se procesara en <0.5s — imperceptible")
    elif estimated_sale_ms < 1000:
        print(f"  Venta: Se procesara en <1s — aceptable")
    elif estimated_sale_ms < 2000:
        print(f"  Venta: Se procesara en <2s — perceptible pero tolerable")
    else:
        print(f"  Venta: Se procesara en >{estimated_sale_ms/1000:.1f}s — NECESITA OPTIMIZACION")
    
    return estimated_sale_ms


def run_tests():
    """Ejecuta todas las pruebas de latencia."""
    env_name = getattr(settings, 'CLOUD_ENVIRONMENT', 'unknown')
    db_config = settings.DATABASES['default']
    
    print("=" * 60)
    print(f"  PRUEBA DE LATENCIA — {env_name.upper()}")
    print("=" * 60)
    print(f"  Fecha:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Engine:   {db_config['ENGINE']}")
    print(f"  Host:     {db_config['HOST']}")
    print(f"  DB:       {db_config['NAME']}")
    print(f"  SSL:      {db_config.get('OPTIONS', {}).get('sslmode', 'N/A')}")
    print(f"  Muestras: {NUM_ITERATIONS} por prueba")
    print("=" * 60)
    
    results = {
        'environment': env_name,
        'timestamp': datetime.now().isoformat(),
        'db_host': db_config['HOST'],
        'db_engine': db_config['ENGINE'],
        'tests': {},
    }
    
    # --- Conexion inicial ---
    print("\n[1/4] Midiendo tiempo de conexion (incluye SSL handshake)...")
    try:
        conn_stats = measure_connection_time()
        results['tests']['connection'] = conn_stats
        print(f"      Conexion nueva: avg={conn_stats['avg_ms']:.0f}ms, "
              f"min={conn_stats['min_ms']:.0f}ms, max={conn_stats['max_ms']:.0f}ms")
    except Exception as e:
        print(f"      [ERROR] No se pudo conectar: {e}")
        return
    
    # --- Warmup ---
    print(f"\n[2/4] Warmup ({WARMUP_ITERATIONS} queries descartadas)...")
    with connection.cursor() as cursor:
        for _ in range(WARMUP_ITERATIONS):
            cursor.execute('SELECT 1')
            cursor.fetchone()
    print("      OK")
    
    # --- Queries SQL directas ---
    print(f"\n[3/4] Queries SQL directas ({NUM_ITERATIONS} iteraciones c/u)...")
    with connection.cursor() as cursor:
        for test_name, test_config in TEST_QUERIES.items():
            try:
                stats = measure_query(cursor, test_config['sql'])
                results['tests'][test_name] = {
                    **stats,
                    'descripcion': test_config['descripcion'],
                }
                print(f"      {test_config['descripcion']}")
                print(f"        avg={stats['avg_ms']:.1f}ms  median={stats['median_ms']:.1f}ms  "
                      f"p95={stats['p95_ms']:.1f}ms  stdev={stats['stdev_ms']:.1f}ms")
            except Exception as e:
                print(f"      {test_name}: [ERROR] {e}")
                results['tests'][test_name] = {'error': str(e)}
    
    # --- Django ORM ---
    print(f"\n[4/4] Queries Django ORM ({NUM_ITERATIONS} iteraciones c/u)...")
    try:
        orm_results = measure_django_orm()
        results['tests'].update(orm_results)
        for name, stats in orm_results.items():
            print(f"      {stats['descripcion']}")
            print(f"        avg={stats['avg_ms']:.1f}ms  median={stats['median_ms']:.1f}ms  "
                  f"p95={stats['p95_ms']:.1f}ms")
    except Exception as e:
        print(f"      [ERROR] ORM: {e}")
    
    # --- Evaluacion UX ---
    ping_avg = results['tests'].get('ping', {}).get('avg_ms', 0)
    orm_avg = results['tests'].get('orm_select_limit5', {}).get('avg_ms', 0)
    if ping_avg and orm_avg:
        estimated = evaluate_pos_impact(ping_avg, orm_avg)
        results['pos_impact'] = {
            'estimated_sale_ms': round(estimated, 2),
            'ping_avg_ms': ping_avg,
            'orm_avg_ms': orm_avg,
        }
    
    # --- Guardar resultados ---
    docs_dir = BASE_DIR / 'docs'
    docs_dir.mkdir(exist_ok=True)
    
    fecha = datetime.now().strftime('%Y%m%d_%H%M')
    output_file = docs_dir / f'latency_results_{env_name}_{fecha}.json'
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'=' * 60}")
    print(f"  Resultados guardados en: {output_file}")
    print(f"{'=' * 60}")
    
    return results


if __name__ == '__main__':
    run_tests()
