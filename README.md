# Sistema POS - Inventario FIFO

Sistema de punto de venta con gestión de inventario mediante método FIFO (First In, First Out).

## 🚀 Tecnologías

- **Backend**: Django 5.0
- **Base de datos**: PostgreSQL 15+
- **Frontend**: Django Templates + Tailwind CSS + Alpine.js
- **Python**: 3.11+

## 📋 Requisitos

- Python 3.11+
- PostgreSQL 15+
- Conda (gestor de paquetes)

## 🔧 Instalación

### 1. Crear ambiente conda

```bash
conda create -n pos_fifo python=3.11 -y
conda activate pos_fifo
```

### 2. Instalar dependencias

```bash
conda install django=5.0 psycopg2 pillow -c conda-forge -y
pip install reportlab python-barcode openpyxl
```

### 3. Configurar base de datos

Crear base de datos en PostgreSQL:

```sql
CREATE DATABASE pos_fifo_db;
CREATE USER pos_user WITH PASSWORD 'Prueba123';
GRANT ALL PRIVILEGES ON DATABASE pos_fifo_db TO pos_user;
```

### 4. Aplicar migraciones

```bash
python manage.py migrate
```

### 5. Crear superusuario

```bash
python manage.py createsuperuser
```

### 6. Ejecutar servidor

```bash
python manage.py runserver
```

Acceder a: http://localhost:8000

## 📁 Estructura del Proyecto

```
pos_fifo_system/
├── config/              # Configuración Django
├── apps/
│   ├── usuarios/        # Gestión de usuarios y roles
│   ├── productos/       # Catálogo de productos
│   ├── inventario/      # Compras, lotes FIFO, ajustes
│   ├── ventas/          # POS, ventas, pagos
│   ├── reportes/        # Reportes y exportaciones
│   └── auditoria/       # Logs y auditoría
├── templates/           # Templates HTML
├── static/              # CSS, JS, imágenes
└── utils/               # Utilidades (impresoras, backups)
```

## 🎯 Módulos

### Sprint 1-2: Fundación ✅
- [ ] Setup proyecto
- [ ] Modelos base: Usuario, Categoría, Producto
- [ ] Autenticación y roles
- [ ] Admin Django

### Sprint 3-4: Inventario FIFO
- [ ] Modelos: Compra, Lote, MovimientoLote
- [ ] Lógica FIFO
- [ ] Gestión de compras
- [ ] Ajustes de inventario

### Sprint 5-6: POS
- [ ] Interfaz punto de venta
- [ ] Carrito de compras
- [ ] Pagos múltiples
- [ ] Integración escáner

### Sprint 7: Reportes e Impresión
- [ ] Cierre de caja
- [ ] Reportes de ventas
- [ ] Impresión tickets

## 📝 Comandos Útiles

```bash
# Crear nueva app
python manage.py startapp nombre_app

# Crear migraciones
python manage.py makemigrations

# Aplicar migraciones
python manage.py migrate

# Ejecutar shell de Django
python manage.py shell

# Recolectar archivos estáticos
python manage.py collectstatic
```

## 🔐 Roles del Sistema

- **Administrador**: Control total del sistema
- **Cajera**: Ventas, descuentos, anulaciones, reimpresión

## 📞 Soporte

Desarrollado para gestión de tienda de artículos varios con control FIFO de inventario.
