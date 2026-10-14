#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
import subprocess
import tempfile
import uuid
import zipfile
import threading
import time
import base64
import re
import webbrowser
import socket
import random
import ast
import json
import traceback
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS

# ============================================================
# CONFIGURACIÓN
# ============================================================

def encontrar_puerto_libre(puerto_inicial=None):
    if puerto_inicial is None:
        puerto_inicial = random.randint(5000, 9000)
    
    max_intentos = 100
    for i in range(max_intentos):
        puerto = puerto_inicial + i
        if puerto > 9000:
            puerto = 5000 + (puerto - 9000)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', puerto))
                return puerto
        except OSError:
            continue
    raise RuntimeError("No se encontró un puerto libre")

PORT = encontrar_puerto_libre()
PUERTO_PROYECTO = encontrar_puerto_libre(PORT + random.randint(100, 500))

os.makedirs('uploads', exist_ok=True)
os.makedirs('outputs', exist_ok=True)
os.makedirs('icons', exist_ok=True)

print(f"🔌 Puerto del convertidor: {PORT}")
print(f"🔌 Puerto del proyecto generado: {PUERTO_PROYECTO}")

# ============================================================
# CREAR app FLASK
# ============================================================

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB
app.config['SECRET_KEY'] = 'convertidor-secreto-2024'

# ============================================================
# ANALIZADOR DE CÓDIGO PYTHON (CORREGIDO)
# ============================================================

@app.route('/api/analyze', methods=['POST'])
def analyze_code():
    """Analiza el código Python de un archivo"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No se recibió ningún archivo'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'Archivo vacío'}), 400
        
        # Leer el contenido con manejo de errores
        try:
            contenido = file.read().decode('utf-8', errors='replace')
        except Exception as e:
            return jsonify({
                'errores': [{'linea': 1, 'mensaje': f'Error al leer el archivo: {str(e)}', 'tipo': 'read_error'}],
                'advertencias': [],
                'info': [],
                'total_errores': 1,
                'total_advertencias': 0,
                'valido': False
            }), 200
        
        # Si el contenido está vacío
        if not contenido or contenido.strip() == '':
            return jsonify({
                'errores': [{'linea': 1, 'mensaje': 'El archivo está vacío', 'tipo': 'empty_file'}],
                'advertencias': [],
                'info': [],
                'total_errores': 1,
                'total_advertencias': 0,
                'valido': False
            }), 200
        
        errores = []
        advertencias = []
        info = []
        lineas = contenido.split('\n')
        
        # Verificar sintaxis con AST
        try:
            arbol = ast.parse(contenido, filename=file.filename)
        except SyntaxError as e:
            errores.append({
                'linea': e.lineno or 1,
                'columna': e.offset or 0,
                'mensaje': f"Error de sintaxis: {e.msg}",
                'texto': lineas[e.lineno - 1] if e.lineno and e.lineno <= len(lineas) else '',
                'tipo': 'syntax_error'
            })
        except ValueError as e:
            errores.append({
                'linea': 1,
                'columna': 0,
                'mensaje': f"Error de codificación: {str(e)}",
                'texto': '',
                'tipo': 'encoding_error'
            })
        except Exception as e:
            errores.append({
                'linea': 1,
                'columna': 0,
                'mensaje': f"Error al analizar: {str(e)}",
                'texto': '',
                'tipo': 'parse_error'
            })
        
        # Verificar caracteres problemáticos (solo si no hay errores graves)
        if not errores:
            for i, linea in enumerate(lineas, 1):
                # Verificar emojis
                try:
                    emojis = re.findall(r'[\U0001F000-\U0001FFFF\u2600-\u27BF\u{1F300}-\u{1F6FF}]', linea, re.UNICODE)
                    if emojis:
                        advertencias.append({
                            'linea': i,
                            'columna': 0,
                            'mensaje': f"Emoji detectado: {', '.join(set(emojis))}",
                            'texto': linea[:50] + ('...' if len(linea) > 50 else ''),
                            'tipo': 'emoji_warning'
                        })
                except:
                    pass
                
                # Verificar caracteres HTML de resaltado
                if any(c in linea for c in ['#ce9178', '#c586c0', '&gt;', '&lt;', 'gu1=']):
                    errores.append({
                        'linea': i,
                        'columna': 0,
                        'mensaje': 'Caracteres HTML o de resaltado en el código',
                        'texto': linea[:50] + ('...' if len(linea) > 50 else ''),
                        'tipo': 'format_error'
                    })
                
                # Verificar caracteres de control
                if re.search(r'[\x00-\x08\x0B\x0C\x0E-\x1F]', linea):
                    errores.append({
                        'linea': i,
                        'columna': 0,
                        'mensaje': 'Caracteres de control en el código',
                        'texto': linea[:50] + ('...' if len(linea) > 50 else ''),
                        'tipo': 'control_char'
                    })
        
        return jsonify({
            'errores': errores,
            'advertencias': advertencias,
            'info': info,
            'total_errores': len(errores),
            'total_advertencias': len(advertencias),
            'valido': len(errores) == 0
        })
    except Exception as e:
        print(f"❌ Error en analyze_code: {e}")
        traceback.print_exc()
        return jsonify({
            'errores': [{'linea': 1, 'mensaje': f'Error interno: {str(e)}', 'tipo': 'internal_error'}],
            'advertencias': [],
            'info': [],
            'total_errores': 1,
            'total_advertencias': 0,
            'valido': False
        }), 200

# ============================================================
# FUNCIONES DE CONVERSIÓN
# ============================================================

def obtener_estructura_carpetas(ruta_base):
    """Obtiene la estructura completa de carpetas y archivos"""
    estructura = {
        'nombre': os.path.basename(ruta_base),
        'carpetas': [],
        'archivos': [],
        'total_archivos': 0,
        'total_carpetas': 0
    }
    
    for root, dirs, files in os.walk(ruta_base):
        # Excluir carpetas comunes
        dirs[:] = [d for d in dirs if d not in ['__pycache__', '.git', '.vscode', 'node_modules', '.idea', 'dist', 'build']]
        
        rel_path = os.path.relpath(root, ruta_base)
        if rel_path == '.':
            rel_path = ''
        
        # Contar carpetas
        if rel_path:
            estructura['carpetas'].append(rel_path.replace('\\', '/'))
        
        # Archivos
        for file in files:
            if file not in ['.DS_Store', 'Thumbs.db']:
                file_path = os.path.join(rel_path, file).replace('\\', '/')
                if file_path.startswith('/'):
                    file_path = file_path[1:]
                estructura['archivos'].append({
                    'ruta': file_path,
                    'nombre': file,
                    'extension': os.path.splitext(file)[1].lower() if '.' in file else '',
                    'es_python': file.endswith('.py')
                })
                estructura['total_archivos'] += 1
    
    estructura['total_carpetas'] = len(estructura['carpetas'])
    return estructura

def limpiar_contenido_python(contenido):
    """Limpia el contenido Python de caracteres problemáticos"""
    # Eliminar emojis
    contenido = re.sub(
        r'[\U0001F000-\U0001FFFF\u2600-\u27BF\u{1F300}-\u{1F6FF}]',
        '',
        contenido,
        flags=re.UNICODE
    )
    
    # Eliminar caracteres de control
    contenido = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', contenido)
    
    # Reemplazar caracteres HTML comunes
    contenido = contenido.replace('&gt;', '>')
    contenido = contenido.replace('&lt;', '<')
    contenido = contenido.replace('&amp;', '&')
    contenido = contenido.replace('&quot;', '"')
    
    # Eliminar marcas de color/resaltado
    contenido = re.sub(r'#[0-9a-fA-F]{6}', '', contenido)
    contenido = re.sub(r'#[0-9a-fA-F]{3}', '', contenido)
    
    return contenido

def limpiar_archivo_python(ruta_archivo):
    """Limpia un archivo Python y devuelve el análisis"""
    try:
        with open(ruta_archivo, 'r', encoding='utf-8', errors='ignore') as f:
            contenido = f.read()
        
        # Limpiar contenido
        contenido_limpio = limpiar_contenido_python(contenido)
        
        # Guardar
        with open(ruta_archivo, 'w', encoding='utf-8') as f:
            f.write(contenido_limpio)
        
        return {'valido': True, 'total_errores': 0, 'total_advertencias': 0, 'errores': [], 'advertencias': []}
    except Exception as e:
        return {
            'errores': [{'linea': 1, 'mensaje': f'Error al leer archivo: {str(e)}'}],
            'total_errores': 1,
            'valido': False
        }

def crear_archivo_spec_portable(nombre, ruta_icono=None, estructura=None):
    """Crea el archivo .spec para PyInstaller con todos los archivos"""
    datas_list = []
    
    if estructura:
        # Agregar todas las carpetas y archivos
        for carpeta in estructura['carpetas']:
            if carpeta and carpeta != 'templates' and not carpeta.startswith('.'):
                datas_list.append(f"('{carpeta}', '{carpeta}')")
        
        # Agregar templates si existe
        if 'templates' in estructura['carpetas'] or any('templates/' in a['ruta'] for a in estructura['archivos']):
            datas_list.append("('templates', 'templates')")
    
    # Si no hay carpetas, al menos incluir templates si existe
    if not datas_list:
        datas_list = ["('templates', 'templates')"]
    
    datas_str = ',\n    '.join(datas_list)
    
    icon_line = ""
    if ruta_icono and os.path.exists(ruta_icono):
        icon_line = f"    icon='{ruta_icono}',"
    
    # Hidden imports comunes
    hidden_imports = [
        'webview', 'flask', 'werkzeug', 'jinja2', 'socket', 
        'threading', 'tempfile', 'shutil', 'webbrowser', 'json'
    ]
    hidden_str = "', '".join(hidden_imports)
    
    return f"""# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        {datas_str}
    ],
    hiddenimports=['{hidden_str}'],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='{nombre}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    {icon_line}
)
"""

def convertir_a_ico(ruta_imagen, ruta_salida=None):
    try:
        from PIL import Image
        
        if ruta_salida is None:
            nombre_base = os.path.splitext(os.path.basename(ruta_imagen))[0]
            ruta_salida = os.path.join('icons', f'{nombre_base}.ico')
        
        img = Image.open(ruta_imagen)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        tamanos = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img.save(ruta_salida, format='ICO', sizes=tamanos)
        return ruta_salida
    except Exception as e:
        print(f"❌ Error al convertir imagen: {e}")
        return None

def convertir_a_exe(carpeta_proyecto, nombre_salida=None, ruta_icono=None):
    """Convierte el proyecto a un ejecutable"""
    try:
        if not nombre_salida:
            nombre_salida = os.path.basename(carpeta_proyecto)
        nombre_salida = ''.join(c for c in nombre_salida if c.isalnum() or c in '._-')
        
        if not nombre_salida:
            nombre_salida = "MiApp"
        
        print("=" * 60)
        print(f"📦 Empaquetando: {nombre_salida}")
        print("📌 Modo PORTABLE - Todo dentro del .exe")
        print(f"📌 Puerto asignado: {PUERTO_PROYECTO}")
        if ruta_icono and os.path.exists(ruta_icono):
            print(f"🖼️ Ícono: {ruta_icono}")
        print("=" * 60)
        
        # Verificar main.py
        main_path = os.path.join(carpeta_proyecto, 'main.py')
        if not os.path.exists(main_path):
            return None, "No se encontró main.py en la carpeta"
        
        # Limpiar main.py
        print("🧹 Analizando y limpiando main.py...")
        reporte = limpiar_archivo_python(main_path)
        
        # Obtener estructura de carpetas
        estructura = obtener_estructura_carpetas(carpeta_proyecto)
        print(f"📁 Carpetas encontradas: {estructura['total_carpetas']}")
        print(f"📄 Archivos encontrados: {estructura['total_archivos']}")
        
        for carpeta in estructura['carpetas'][:10]:
            print(f"   📂 {carpeta}")
        if len(estructura['carpetas']) > 10:
            print(f"   ... y {len(estructura['carpetas']) - 10} carpetas más")
        
        # Copiar ícono si existe
        if ruta_icono and os.path.exists(ruta_icono):
            shutil.copy2(ruta_icono, carpeta_proyecto)
        
        # Crear directorio temporal
        temp_id = str(uuid.uuid4())[:8]
        temp_folder = os.path.join(os.path.dirname(__file__), 'uploads', temp_id)
        os.makedirs(temp_folder, exist_ok=True)
        
        temp_project = os.path.join(temp_folder, 'project')
        shutil.copytree(carpeta_proyecto, temp_project, dirs_exist_ok=True)
        
        original_dir = os.getcwd()
        os.chdir(temp_project)
        
        # Crear archivo .spec
        spec_path = os.path.join(temp_project, f'{nombre_salida}.spec')
        with open(spec_path, 'w', encoding='utf-8') as f:
            f.write(crear_archivo_spec_portable(nombre_salida, ruta_icono if ruta_icono else None, estructura))
        
        print("🔧 Ejecutando PyInstaller...")
        print("⏳ Esto puede tomar varios minutos...")
        
        comando = [
            sys.executable, "-m", "PyInstaller",
            "--clean",
            "--noconfirm",
            spec_path
        ]
        
        proceso = subprocess.Popen(
            comando,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        for linea in proceso.stdout:
            linea = linea.strip()
            if linea:
                if 'error' in linea.lower() or 'Error' in linea:
                    print(f"   ❌ {linea[:120]}")
                elif 'warning' in linea.lower():
                    print(f"   ⚠️ {linea[:120]}")
                else:
                    print(f"   {linea[:120]}")
        
        proceso.wait()
        
        os.chdir(original_dir)
        
        if proceso.returncode != 0:
            return None, f"Error en PyInstaller (codigo {proceso.returncode})"
        
        exe_path = os.path.join(temp_project, 'dist', f'{nombre_salida}.exe')
        
        if os.path.exists(exe_path):
            output_dir = os.path.join(os.path.dirname(__file__), 'outputs')
            os.makedirs(output_dir, exist_ok=True)
            
            output_path = os.path.join(output_dir, f'{nombre_salida}.exe')
            shutil.copy2(exe_path, output_path)
            
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            
            print("=" * 60)
            print(f"✅ ¡Ejecutable PORTABLE creado!")
            print(f"📁 {output_path}")
            print(f"📦 Tamaño: {size_mb:.2f} MB")
            print(f"📁 Carpetas empaquetadas: {estructura['total_carpetas']}")
            print(f"📄 Archivos empaquetados: {estructura['total_archivos']}")
            if ruta_icono and os.path.exists(ruta_icono):
                print(f"🖼️ Ícono aplicado")
            print("=" * 60)
            
            return output_path, f"Ejecutable PORTABLE creado ({size_mb:.2f} MB)"
        else:
            return None, "No se encontró el ejecutable"
            
    except Exception as e:
        return None, f"Error: {str(e)}"

# ============================================================
# RUTAS FLASK
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/port')
def get_port():
    return jsonify({'port': PORT})

@app.route('/api/logs', methods=['GET'])
def get_logs():
    return jsonify({'logs': []})

@app.route('/api/upload', methods=['POST'])
def upload_folder():
    try:
        print("📥 Recibiendo archivo...")
        
        if 'folder' not in request.files:
            return jsonify({'error': 'No se recibió ningún archivo'}), 400
        
        file = request.files['folder']
        if file.filename == '':
            return jsonify({'error': 'Archivo vacío'}), 400
        
        print(f"📄 Archivo: {file.filename}")
        
        temp_id = str(uuid.uuid4())[:8]
        temp_folder = os.path.join('uploads', temp_id)
        os.makedirs(temp_folder, exist_ok=True)
        
        zip_path = os.path.join(temp_folder, 'project.zip')
        file.save(zip_path)
        print(f"💾 ZIP guardado: {zip_path}")
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_folder)
        print("📂 ZIP extraído")
        
        extracted_folders = [f for f in os.listdir(temp_folder) 
                           if os.path.isdir(os.path.join(temp_folder, f)) and f != '__MACOSX']
        
        if not extracted_folders:
            return jsonify({'error': 'El ZIP no contiene carpetas'}), 400
        
        project_path = os.path.join(temp_folder, extracted_folders[0])
        print(f"📁 Proyecto en: {project_path}")
        
        nombre = request.form.get('nombre', None)
        icono = request.form.get('icono', None)
        
        ruta_icono = None
        if icono and icono.startswith('data:image'):
            try:
                header, data = icono.split(',', 1)
                image_data = base64.b64decode(data)
                
                temp_icon = os.path.join('icons', f'icon_{temp_id}.png')
                with open(temp_icon, 'wb') as f:
                    f.write(image_data)
                
                ruta_icono = convertir_a_ico(temp_icon)
                if ruta_icono:
                    print(f"🖼️ Ícono convertido: {ruta_icono}")
            except Exception as e:
                print(f"⚠️ Error al procesar ícono: {e}")
        
        exe_path, mensaje = convertir_a_exe(project_path, nombre, ruta_icono)
        
        if exe_path:
            return jsonify({
                'success': True,
                'message': mensaje,
                'download_url': f'/download/{os.path.basename(exe_path)}',
                'port': PUERTO_PROYECTO
            })
        else:
            return jsonify({'error': mensaje}), 400
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({'error': f'Error al procesar: {str(e)}'}), 500

@app.route('/download/<filename>')
def download_file(filename):
    try:
        file_path = os.path.join('outputs', filename)
        if not os.path.exists(file_path):
            return jsonify({'error': 'Archivo no encontrado'}), 404
        return send_file(file_path, as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'error': f'Error al descargar: {str(e)}'}), 500

# ============================================================
# INICIO
# ============================================================

def abrir_convertidor():
    try:
        import webview
        
        print("=" * 60)
        print("🚀 Convertidor a .exe PORTABLE")
        print("📦 Todo DENTRO del .exe")
        print(f"🔌 Puerto: {PORT}")
        print(f"🌐 URL: http://127.0.0.1:{PORT}")
        print("=" * 60)
        
        window = webview.create_window(
            'Convertidor a .exe',
            url=f'http://127.0.0.1:{PORT}',
            width=1100,
            height=750,
            resizable=True,
            fullscreen=False,
            min_size=(800, 650),
            confirm_close=True,
            text_select=True,
            easy_drag=True
        )
        
        webview.start(gui='edgechromium', debug=False)
        
    except ImportError:
        print("❌ pywebview no está instalado")
        print("📦 Instala con: pip install pywebview")
        print("💡 Abriendo en navegador...")
        webbrowser.open(f'http://127.0.0.1:{PORT}')
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sys.exit(0)
    except Exception as e:
        print(f"❌ Error al abrir: {e}")
        webbrowser.open(f'http://127.0.0.1:{PORT}')
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sys.exit(0)

def iniciar_servidor_flask():
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False, threaded=True)

def main():
    print("=" * 60)
    print("🚀 Convertidor a .exe PORTABLE")
    print("📦 Todo DENTRO del .exe")
    print("🔍 Buscando puertos...")
    print(f"🔌 Puerto del convertidor: {PORT}")
    print("=" * 60)
    
    templates_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    if not os.path.exists(templates_path):
        print("❌ ERROR: No se encuentra la carpeta 'templates'")
        print(f"📁 Buscando en: {templates_path}")
        input("Presiona Enter para salir...")
        return
    
    index_path = os.path.join(templates_path, 'index.html')
    if not os.path.exists(index_path):
        print("❌ ERROR: No se encuentra index.html")
        print(f"📁 Buscando en: {index_path}")
        input("Presiona Enter para salir...")
        return
    
    print(f"📁 Sirviendo desde: {templates_path}")
    print("🔄 Iniciando servidor Flask...")
    
    flask_thread = threading.Thread(target=iniciar_servidor_flask, daemon=True)
    flask_thread.start()
    
    time.sleep(2)
    print("🖥️ Abriendo convertidor...")
    abrir_convertidor()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹️ Cerrando...")
        sys.exit(0)