import http.server
import socketserver
import os
import signal
import sys
import json
import uuid
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
from urllib.parse import parse_qs, urlparse

# Конфигурация
YOUR_INDEX_FILE = 'my_page.html'
PORT = 6767
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(DIRECTORY, 'uploads')
METADATA_FILE = os.path.join(DIRECTORY, 'files_metadata.json')

# Создаем папку для загрузок если её нет
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

class FileMetadata:
    """Класс для работы с метаданными файлов"""
    
    def __init__(self):
        self.metadata_file = METADATA_FILE
        self.metadata = self.load_metadata()
    
    def load_metadata(self):
        """Загружает метаданные из файла"""
        if os.path.exists(self.metadata_file):
            try:
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_metadata(self):
        """Сохраняет метаданные в файл"""
        with open(self.metadata_file, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, indent=2, ensure_ascii=False)
    
    def add_file(self, filename, original_name, size, description=""):
        """Добавляет информацию о файле"""
        file_id = str(uuid.uuid4())
        self.metadata[file_id] = {
            'id': file_id,
            'filename': filename,
            'original_name': original_name,
            'size': size,
            'description': description,
            'upload_date': datetime.now().isoformat(),
            'downloads': 0
        }
        self.save_metadata()
        return file_id
    
    def get_file(self, file_id):
        """Получает информацию о файле"""
        return self.metadata.get(file_id)
    
    def get_all_files(self):
        """Получает список всех файлов"""
        files = []
        for file_id, info in self.metadata.items():
            files.append({
                'id': file_id,
                'original_name': info['original_name'],
                'size': info['size'],
                'description': info.get('description', ''),
                'upload_date': info['upload_date'],
                'downloads': info.get('downloads', 0)
            })
        # Сортируем по дате (новые сначала)
        files.sort(key=lambda x: x['upload_date'], reverse=True)
        return files
    
    def delete_file(self, file_id):
        """Удаляет информацию о файле"""
        if file_id in self.metadata:
            file_info = self.metadata[file_id]
            file_path = os.path.join(UPLOAD_FOLDER, file_info['filename'])
            if os.path.exists(file_path):
                os.remove(file_path)
            del self.metadata[file_id]
            self.save_metadata()
            return True
        return False
    
    def increment_downloads(self, file_id):
        """Увеличивает счетчик скачиваний"""
        if file_id in self.metadata:
            self.metadata[file_id]['downloads'] = self.metadata[file_id].get('downloads', 0) + 1
            self.save_metadata()

# Инициализация метаданных
file_metadata = FileMetadata()

def parse_multipart_form_data(content_type, body):
    """Парсит multipart/form-data без использования cgi"""
    # Находим границу
    boundary = None
    for part in content_type.split(';'):
        part = part.strip()
        if part.startswith('boundary='):
            boundary = part[9:].strip('"')
            break
    
    if not boundary:
        return None
    
    # Разделяем тело запроса по границе
    boundary_bytes = boundary.encode('utf-8')
    parts = body.split(b'--' + boundary_bytes)
    
    result = {'fields': {}, 'files': {}}
    
    for part in parts:
        if not part or part == b'--' or part == b'--\r\n':
            continue
        
        # Удаляем завершающие \r\n
        part = part.lstrip(b'\r\n').rstrip(b'\r\n--')
        
        # Находим разделитель между заголовками и телом
        header_end = part.find(b'\r\n\r\n')
        if header_end == -1:
            continue
        
        headers_raw = part[:header_end].decode('utf-8', errors='ignore')
        body_content = part[header_end + 4:]
        
        # Парсим заголовки
        headers = {}
        for line in headers_raw.split('\r\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                headers[key.strip().lower()] = value.strip()
        
        # Извлекаем имя поля и имя файла из Content-Disposition
        disposition = headers.get('content-disposition', '')
        
        field_name = None
        filename = None
        
        for param in disposition.split(';'):
            param = param.strip()
            if param.startswith('name='):
                field_name = param[5:].strip('"')
            elif param.startswith('filename='):
                filename = param[9:].strip('"')
        
        if not field_name:
            continue
        
        if filename:
            # Это файл
            result['files'][field_name] = {
                'filename': filename,
                'content': body_content,
                'content_type': headers.get('content-type', 'application/octet-stream')
            }
        else:
            # Это обычное поле
            result['fields'][field_name] = body_content.decode('utf-8', errors='ignore')
    
    return result

class FileShareHandler(http.server.SimpleHTTPRequestHandler):
    """Обработчик HTTP-запросов для файлообменника"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)
    
    def do_GET(self):
        """Обрабатывает GET-запросы"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        # API endpoints
        if path == '/api/files':
            self.handle_get_files()
            return
        elif path.startswith('/api/download/'):
            file_id = path.split('/')[-1]
            self.handle_download(file_id)
            return
        elif path.startswith('/api/delete/'):
            file_id = path.split('/')[-1]
            self.handle_delete(file_id)
            return
        
        # Статические файлы
        if path == '/' or path == '':
            path = f'/{YOUR_INDEX_FILE}'
        
        # Сохраняем оригинальный путь и обрабатываем
        original_path = self.path
        self.path = path
        
        try:
            super().do_GET()
        except:
            self.path = original_path
            super().do_GET()
    
    def do_POST(self):
        """Обрабатывает POST-запросы"""
        if self.path == '/api/upload':
            self.handle_upload()
            return
        
        self.send_error(404, "Not found")
    
    def handle_get_files(self):
        """Возвращает список файлов в JSON формате"""
        try:
            files = file_metadata.get_all_files()
            self.send_json_response(files)
        except Exception as e:
            self.send_json_response({'error': str(e)}, 500)
    
    def handle_upload(self):
        """Обрабатывает загрузку файла"""
        try:
            content_type = self.headers.get('Content-Type', '')
            
            if 'multipart/form-data' in content_type:
                # Получаем тело запроса
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length)
                
                # Парсим multipart форму
                parsed_data = parse_multipart_form_data(content_type, body)
                
                if not parsed_data:
                    self.send_json_response({'error': 'Не удалось разобрать форму'}, 400)
                    return
                
                file_data = parsed_data['files'].get('file')
                description = parsed_data['fields'].get('description', '')
                
                if file_data and file_data['filename']:
                    original_name = file_data['filename']
                    file_ext = os.path.splitext(original_name)[1]
                    unique_filename = f"{uuid.uuid4()}{file_ext}"
                    
                    # Сохраняем файл
                    file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
                    with open(file_path, 'wb') as f:
                        f.write(file_data['content'])
                    
                    # Получаем размер файла
                    file_size = os.path.getsize(file_path)
                    
                    # Добавляем в метаданные
                    file_id = file_metadata.add_file(
                        unique_filename, 
                        original_name, 
                        file_size,
                        description
                    )
                    
                    # Отправляем ответ
                    response_data = {
                        'success': True,
                        'file_id': file_id,
                        'original_name': original_name,
                        'size': file_size
                    }
                    self.send_json_response(response_data)
                    
                    # Выводим информацию о загрузке
                    print(f"✅ Файл загружен: {original_name} ({self.format_size(file_size)})")
                else:
                    self.send_json_response({'error': 'Файл не выбран'}, 400)
            else:
                self.send_json_response({'error': 'Неверный тип контента'}, 400)
                
        except Exception as e:
            self.send_json_response({'error': str(e)}, 500)
            print(f"❌ Ошибка загрузки: {e}")
    
    def handle_download(self, file_id):
        """Обрабатывает скачивание файла"""
        file_info = file_metadata.get_file(file_id)
        
        if not file_info:
            self.send_error(404, "File not found")
            return
        
        file_path = os.path.join(UPLOAD_FOLDER, file_info['filename'])
        
        if not os.path.exists(file_path):
            self.send_error(404, "File not found on disk")
            return
        
        try:
            # Увеличиваем счетчик скачиваний
            file_metadata.increment_downloads(file_id)
            
            # Отправляем файл
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            # Кодируем имя файла для безопасной передачи
            encoded_filename = file_info["original_name"].encode('utf-8')
            self.send_header('Content-Disposition', 
                           f'attachment; filename*=UTF-8\'\'{encoded_filename.decode("utf-8")}')
            self.send_header('Content-Length', str(file_info['size']))
            self.end_headers()
            
            with open(file_path, 'rb') as f:
                shutil.copyfileobj(f, self.wfile)
            
            print(f"📥 Скачан файл: {file_info['original_name']}")
            
        except Exception as e:
            self.send_error(500, f"Error downloading file: {str(e)}")
    
    def handle_delete(self, file_id):
        """Обрабатывает удаление файла"""
        if file_metadata.delete_file(file_id):
            self.send_json_response({'success': True, 'message': 'File deleted'})
            print(f"🗑️ Файл удален: {file_id}")
        else:
            self.send_json_response({'error': 'File not found'}, 404)
    
    def send_json_response(self, data, status=200):
        """Отправляет JSON ответ"""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    
    def format_size(self, size_bytes):
        """Форматирует размер файла"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"
    
    def log_message(self, format, *args):
        """Улучшенное логирование"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        status_code = args[1] if len(args) > 1 else '---'
        
        color = '\033[92m' if str(status_code).startswith('2') else \
                '\033[93m' if str(status_code).startswith('3') else \
                '\033[91m' if str(status_code).startswith(('4', '5')) else '\033[0m'
        
        print(f"{timestamp} | {color}{status_code}\033[0m | {self.client_address[0]} | {args[0]}")

def signal_handler(sig, frame):
    """Обработчик для корректного завершения"""
    print("\n\nСервер останавливается...")
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    socketserver.TCPServer.allow_reuse_address = True
    
    try:
        with socketserver.TCPServer(("", PORT), FileShareHandler) as httpd:
            print("=" * 60)
            print(f"🚀 Файлообменник запущен на порту {PORT}")
            print(f"📁 Папка загрузок: {UPLOAD_FOLDER}")
            print(f"🌐 URL: http://localhost:{PORT}")
            print(f"📋 Всего файлов: {len(file_metadata.get_all_files())}")
            print("=" * 60)
            print("Нажмите Ctrl+C для остановки сервера\n")
            
            httpd.serve_forever()
            
    except OSError as e:
        if e.errno == 98:
            print(f"❌ Порт {PORT} уже используется!")
        else:
            print(f"❌ Ошибка: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Неожиданная ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()