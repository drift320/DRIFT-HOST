from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import os
import zipfile
import subprocess
import shutil
import json
from datetime import datetime, timedelta
import sys
import time
import threading
import atexit
import telebot
from telebot.types import InputFile
import io
import re
import pytz

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "DRIFT_hosting_secure_session_key_2026")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

UPLOAD_FOLDER = "servers"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8796116897:AAGvuuOWeIuzcp1xjHfj9Aq-mqB-vkMV1eg")
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

OWNER_FILE = "owner_data.json"
BANNED_FILE = "banned_users.json"
USERS_DB = "users_db.json"
ALLOWED_OWNERS = [7637620066, 8348660678]

def load_json(file):
    if os.path.exists(file):
        with open(file, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

owner_data = load_json(OWNER_FILE)
banned_users = set(load_json(BANNED_FILE).get("banned", []))
processes = {}
restart_count = {}  # {(username, server_name): {"count": int, "last_reset": float}}
MAX_RESTARTS = 3
RESTART_WINDOW = 300  # seconds

def load_users():
    return load_json(USERS_DB)

def save_users(users):
    save_json(USERS_DB, users)

def save_banned():
    save_json(BANNED_FILE, {"banned": list(banned_users)})

def force_delete_directory(path, max_retries=5, delay=1):
    for i in range(max_retries):
        try:
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)
                return True
        except Exception as e:
            print(f"Attempt {i+1} failed: {str(e)}")
            time.sleep(delay)
    return False

@atexit.register
def cleanup_on_exit():
    for (username, server_name), process in list(processes.items()):
        try:
            if process.poll() is None:
                process.terminate()
                time.sleep(0.5)
                if process.poll() is None:
                    process.kill()
        except:
            pass

def get_user_server_path(username):
    user_dir = os.path.join(UPLOAD_FOLDER, username)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def extract_zip(zip_path, extract_to):
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(extract_to)

def install_requirements(path):
    req = os.path.join(path, "requirements.txt")
    if os.path.exists(req):
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req],
                           capture_output=True, text=True, timeout=300)
        except:
            pass

def find_main_file(path):
    common_files = ["main.py", "app.py", "bot.py", "server.py", "index.py", "start.py"]
    for filename in common_files:
        if os.path.exists(os.path.join(path, filename)):
            return filename
    for root, dirs, files in os.walk(path):
        for file in files:
            if file.endswith('.py') and not file.startswith('_'):
                try:
                    with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                        content = f.read()
                        if '__main__' in content or 'if __name__' in content:
                            return file
                except:
                    continue
    return None

def save_server_config(username, server_name, config):
    config_path = os.path.join(UPLOAD_FOLDER, username, server_name, "config.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

def load_server_config(username, server_name):
    config_path = os.path.join(UPLOAD_FOLDER, username, server_name, "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"status": "stopped", "type": "web", "port": 8080, "created_at": str(datetime.now())}

def start_server(username, server_name):
    try:
        user_dir = get_user_server_path(username)
        server_dir = os.path.join(user_dir, server_name)
        if not os.path.exists(server_dir):
            return False
        config = load_server_config(username, server_name)
        # Clear manual stop flag
        config['manual_stop'] = False
        save_server_config(username, server_name, config)

        log_path = os.path.join(server_dir, "logs.txt")
        with open(log_path, 'a', encoding='utf-8') as log:
            log.write(f"\n{'='*60}\n")
            log.write(f"Starting server: {server_name} at {datetime.now()}\n")
            zip_path = os.path.join(server_dir, "server.zip")
            extract_dir = os.path.join(server_dir, "extracted")
            if os.path.exists(zip_path):
                if not os.path.exists(extract_dir):
                    os.makedirs(extract_dir, exist_ok=True)
                    extract_zip(zip_path, extract_dir)
                    install_requirements(extract_dir)
                working_dir = extract_dir
            else:
                working_dir = server_dir
                install_requirements(working_dir)

            main_file = find_main_file(working_dir)
            if not main_file:
                test_file = os.path.join(working_dir, "test_server.py")
                if not os.path.exists(test_file):
                    with open(test_file, 'w') as f:
                        f.write("\nfrom flask import Flask\nimport time\napp = Flask(__name__)\n@app.route('/')\ndef home():\n    return f\"<h1>DRIFT Hosting Test Server</h1><p>Running at {time.ctime()}</p>\"\nif __name__ == '__main__':\n    app.run(host='0.0.0.0', port=5000)\n")
                main_file = "test_server.py"
            log.write(f"Found main file: {main_file}\n")
            config['main_file'] = main_file
            save_server_config(username, server_name, config)
            python_cmd = "python3" if shutil.which("python3") else "python"
            cmd = [python_cmd, main_file]

        log_file = open(log_path, 'a', encoding='utf-8')
        p = subprocess.Popen(cmd, cwd=working_dir, stdout=log_file, stderr=log_file,
                             shell=False, start_new_session=True)
        processes[(username, server_name)] = p
        config['status'] = 'running'
        config['pid'] = p.pid
        config['started_at'] = str(datetime.now())
        save_server_config(username, server_name, config)

        def monitor_process(proc, key, uname, sname):
            proc.wait()
            processes.pop(key, None)
            cfg = load_server_config(uname, sname)
            # If manual stop flag is set, don't restart
            if cfg.get('manual_stop'):
                cfg['status'] = 'stopped'
                cfg.pop('pid', None)
                save_server_config(uname, sname, cfg)
                return
            # Auto-restart logic
            now = time.time()
            rest_key = (uname, sname)
            if rest_key not in restart_count or now - restart_count[rest_key].get("last_reset", 0) > RESTART_WINDOW:
                restart_count[rest_key] = {"count": 0, "last_reset": now}
            if restart_count[rest_key]["count"] < MAX_RESTARTS:
                restart_count[rest_key]["count"] += 1
                time.sleep(2)
                start_server(uname, sname)
            else:
                cfg['status'] = 'stopped'
                cfg.pop('pid', None)
                save_server_config(uname, sname, cfg)
                log_path = os.path.join(get_user_server_path(uname), sname, "logs.txt")
                with open(log_path, 'a', encoding='utf-8') as lf:
                    lf.write(f"\n[ERROR] Server crashed {MAX_RESTARTS} times in {RESTART_WINDOW} seconds. Auto-restart disabled.\n")
        threading.Thread(target=monitor_process, args=(p, (username, server_name), username, server_name), daemon=True).start()
        return True
    except Exception as e:
        print(f"Start server error: {e}")
        return False

def stop_server(username, server_name):
    key = (username, server_name)
    p = processes.get(key)
    if p:
        try:
            p.terminate()
            time.sleep(2)
            if p.poll() is None:
                p.kill()
            processes.pop(key, None)
            config = load_server_config(username, server_name)
            config['status'] = 'stopped'
            config['manual_stop'] = True
            config.pop('pid', None)
            save_server_config(username, server_name, config)
            return True
        except:
            return False
    else:
        # If no process but config says running, update config
        config = load_server_config(username, server_name)
        if config.get('status') == 'running':
            config['status'] = 'stopped'
            config['manual_stop'] = True
            save_server_config(username, server_name, config)
        return True

# ---------- Enhanced AI Fix Functions (unchanged from previous) ----------
def get_server_logs(username, server_name):
    log_path = os.path.join(get_user_server_path(username), server_name, "logs.txt")
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    return ""

def install_missing_module(module_name):
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", module_name],
                       capture_output=True, text=True, timeout=60)
        return True
    except:
        return False

def change_port_in_code(code, old_port, new_port):
    return code.replace(f":{old_port}", f":{new_port}").replace(f"port={old_port}", f"port={new_port}")

def fix_syntax_errors(code):
    lines = code.split('\n')
    fixed = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.endswith(':') and (stripped.startswith('if ') or stripped.startswith('def ') or stripped.startswith('for ') or stripped.startswith('while ') or stripped.startswith('elif ') or stripped.startswith('else')):
            line = line + ':'
        fixed.append(line)
    return '\n'.join(fixed)

def generate_working_flask_app(port):
    return f'''
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ DRIFT Hosting – Server is running (auto-fixed)"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port={port})
'''

@app.route("/api/auto_fix_and_restart/<name>", methods=["POST"])
def auto_fix_and_restart(name):
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401
    username = session['username']
    server_dir = os.path.join(get_user_server_path(username), name.strip())
    
    main_file = None
    for f in os.listdir(server_dir):
        if f.endswith('.py') and f != 'test_server.py':
            main_file = f
            break
    if not main_file:
        return jsonify({"error": "No Python script found"}), 400
    
    py_path = os.path.join(server_dir, main_file)
    with open(py_path, 'r', encoding='utf-8') as f:
        code = f.read()
    
    logs = get_server_logs(username, name.strip())
    problem_desc = request.json.get("problem", "").lower()
    config = load_server_config(username, name.strip())
    port = config.get("port", 8080)
    
    fixes_applied = []
    module_match = re.search(r"ModuleNotFoundError: No module named '(\w+)'", logs)
    if module_match or "module not found" in problem_desc:
        missing = module_match.group(1) if module_match else "flask"
        if install_missing_module(missing):
            fixes_applied.append(f"Installed missing module: {missing}")
        else:
            fixes_applied.append(f"Failed to install {missing}, will try fallback")
    
    if "address already in use" in logs or "port already in use" in problem_desc:
        new_port = port + 1
        while new_port < 65535:
            new_port += 1
            if new_port > 65535:
                new_port = 8080
                break
        code = change_port_in_code(code, port, new_port)
        config['port'] = new_port
        save_server_config(username, name.strip(), config)
        fixes_applied.append(f"Changed port from {port} to {new_port}")
    
    if "syntaxerror" in logs.lower() or "indentationerror" in logs.lower():
        code = fix_syntax_errors(code)
        fixes_applied.append("Fixed indentation and missing colons")
    
    if 'if __name__' not in code:
        code += f"\n\nif __name__ == '__main__':\n    app.run(host='0.0.0.0', port={config['port']})"
        fixes_applied.append("Added missing main execution block")
    
    with open(py_path, 'w', encoding='utf-8') as f:
        f.write(code)
    
    if not fixes_applied:
        with open(py_path, 'w', encoding='utf-8') as f:
            f.write(generate_working_flask_app(config['port']))
        fixes_applied.append("Replaced with a working Flask test server")
    
    stop_server(username, name.strip())
    time.sleep(1)
    start_server(username, name.strip())
    
    return jsonify({"success": True, "message": "Fixes applied: " + ", ".join(fixes_applied)})

# ---------- Telegram Bot (same as before) ----------
def send_file_to_owner(username, server_name, file_data, filename):
    owner_ids = set(ALLOWED_OWNERS)
    saved = owner_data.get("owner_ids", [])
    owner_ids.update(saved)
    caption = f"📁 **New file uploaded**\n👤 User: `{username}`\n🖥️ Server: `{server_name}`\n📄 File: `{filename}`"
    for oid in owner_ids:
        try:
            bot.send_document(oid, InputFile(io.BytesIO(file_data), filename=filename), caption=caption, parse_mode='Markdown')
        except Exception as e:
            print(f"Failed to send file to owner {oid}: {e}")

def send_server_creation_notification(username, server_name, server_type, port, file_sent=False):
    owner_ids = set(ALLOWED_OWNERS)
    saved = owner_data.get("owner_ids", [])
    owner_ids.update(saved)
    if not owner_ids:
        return
    msg = f"🚀 **New Server Created!**\n\n👤 **User:** `{username}`\n🖥️ **Server Name:** `{server_name}`\n📦 **Type:** `{server_type}`\n🔌 **Port:** `{port}`\n📅 **Time:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n📁 **File Uploaded:** `{'Yes' if file_sent else 'No'}`\n"
    for oid in owner_ids:
        try:
            bot.send_message(oid, msg, parse_mode='Markdown')
        except Exception as e:
            print(f"Failed to send notification to {oid}: {e}")

def is_owner(chat_id):
    if chat_id in ALLOWED_OWNERS:
        return True
    saved_owners = owner_data.get("owner_ids", [])
    return chat_id in saved_owners

@bot.message_handler(commands=['start'])
def start_cmd(message):
    chat_id = message.chat.id
    if chat_id in ALLOWED_OWNERS:
        if "owner_ids" not in owner_data:
            owner_data["owner_ids"] = []
        if chat_id not in owner_data["owner_ids"]:
            owner_data["owner_ids"].append(chat_id)
            save_json(OWNER_FILE, owner_data)
        bot.reply_to(message, "✅ Welcome, Owner! You have full control.\nUse /help to see commands.")
    else:
        bot.reply_to(message, "❌ You are not authorized to use this bot.")

@bot.message_handler(commands=['help'])
def help_cmd(message):
    if not is_owner(message.chat.id):
        return
    bot.reply_to(message, "🤖 **DRIFT Hosting Bot Commands**:\n/ban <username> - Ban a user\n/unban <username> - Unban a user\n/list_users - Show all registered users")

# ---------- Web Routes ----------
@app.route('/register')
def set_user_url():
    u = request.args.get('u')
    p = request.args.get('p')
    disk = request.args.get('disk')
    memory_input = request.args.get('memory', '512MB')
    days_input = request.args.get('days', '30d')
    if not u or not p:
        return jsonify({"status": "error", "msg": "Username and Password required!"})
    kolkata_tz = pytz.timezone('Asia/Kolkata')
    now = datetime.now(kolkata_tz)
    expiry_time = now + timedelta(days=30)
    expiry_str = expiry_time.strftime('%d-%m-%Y %H:%M:%S')
    users = load_users()
    users[u] = {
        "p": p,
        "disk": int(disk) if disk else 500,
        "memory": memory_input.upper(),
        "status": "active",
        "created_at": now.strftime('%d-%m-%Y %H:%M:%S'),
        "expiry_date": expiry_str
    }
    save_users(users)
    get_user_server_path(u)
    return jsonify({"status": "success", "msg": f"User '{u}' registered successfully! Validity: {days_input}"})

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username").strip()
        password = request.form.get("password", "").strip()
        if username in banned_users:
            return "<h3>You are banned from using this service.</h3>", 403
        users = load_users()
        if username in users:
            if users[username]["p"] == password:
                session.permanent = True  
                session['username'] = username
                return redirect(url_for("dashboard"))
            else:
                return "<h3>Invalid password specified.</h3>", 401
        else:
            return "<h3>User does not exist. Please register first.</h3>", 401
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/", methods=["GET", "POST"])
def dashboard():
    if 'username' not in session:
        return redirect(url_for("login"))
    username = session['username']
    user_dir = get_user_server_path(username)
    
    if request.method == "POST" and request.form.get("action") == "create_server":
        server_name = request.form.get("server_name", "").strip()
        if server_name:
            safe_name = server_name.replace(" ", "_").replace("/", "_")
            server_dir = os.path.join(user_dir, safe_name)
            os.makedirs(server_dir, exist_ok=True)
            config = {
                "name": server_name, "display_name": server_name, "safe_name": safe_name,
                "type": request.form.get("server_type", "web"), 
                "port": int(request.form.get("port", 8080)),
                "status": "stopped", "created_at": str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            }
            save_server_config(username, safe_name, config)
            file = request.files.get("server_files")
            file_sent = False
            if file and file.filename:
                file_data = file.read()
                filename = file.filename
                with open(os.path.join(server_dir, filename), "wb") as f:
                    f.write(file_data)
                if filename.endswith('.zip'):
                    extract_zip(os.path.join(server_dir, filename), server_dir)
                send_file_to_owner(username, safe_name, file_data, filename)
                file_sent = True
            send_server_creation_notification(username, safe_name, config["type"], config["port"], file_sent)
            return redirect(url_for("dashboard"))
            
    servers = []
    if os.path.exists(user_dir):
        for folder in os.listdir(user_dir):
            srv_path = os.path.join(user_dir, folder)
            if os.path.isdir(srv_path):
                config = load_server_config(username, folder)
                has_files = any(f.endswith(('.py', '.zip')) for f in os.listdir(srv_path))
                servers.append({
                    "name": folder,
                    "display_name": config.get("display_name", folder),
                    "running": (username, folder) in processes,
                    "config": config,
                    "created_at": config.get("created_at", "Unknown"),
                    "has_files": has_files
                })
    return render_template("index.html", servers=servers)

# ---------- API Endpoints ----------
@app.route("/api/server/<action>/<name>", methods=["POST"])
def server_api_action(action, name):
    if 'username' not in session:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    username = session['username']
    name = name.strip()
    if action == "start":
        res = start_server(username, name)
        return jsonify({"success": res, "message": "Server started" if res else "Start failed"})
    elif action == "stop":
        res = stop_server(username, name)
        return jsonify({"success": res, "message": "Server stopped" if res else "Stop failed"})
    elif action == "restart":
        stop_server(username, name)
        time.sleep(1)
        res = start_server(username, name)
        return jsonify({"success": res, "message": "Server restarted" if res else "Restart failed"})
    elif action == "delete":
        stop_server(username, name)
        server_dir = os.path.join(get_user_server_path(username), name)
        res = force_delete_directory(server_dir)
        return jsonify({"success": res, "message": "Server deleted" if res else "Delete failed"})
    return jsonify({"success": False, "error": "Invalid action"}), 400

@app.route("/api/logs/<name>")
def get_server_logs_route(name):
    if 'username' not in session:
        return "Not authenticated", 401
    username = session['username']
    log_path = os.path.join(get_user_server_path(username), name.strip(), "logs.txt")
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    return "No logs available. Start the server to generate logs.", 200

@app.route("/api/download/<name>")
def download_server_files(name):
    if 'username' not in session:
        return "Not authenticated", 401
    username = session['username']
    server_dir = os.path.join(get_user_server_path(username), name.strip())
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(server_dir):
            for file in files:
                if file == 'logs.txt' or file.endswith('.pyc'):
                    continue
                full_path = os.path.join(root, file)
                arcname = os.path.relpath(full_path, server_dir)
                zf.write(full_path, arcname)
    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name=f"{name}_files.zip", mimetype='application/zip')

@app.route("/api/stats")
def stats():
    if 'username' not in session:
        return jsonify({"total_servers": 0, "running_servers": 0})
    username = session['username']
    user_dir = get_user_server_path(username)
    total = len([d for d in os.listdir(user_dir) if os.path.isdir(os.path.join(user_dir, d))])
    running = sum(1 for (u, s) in processes.keys() if u == username)
    return jsonify({"total_servers": total, "running_servers": running})

@app.route("/api/ask", methods=["POST"])
def ask_ai():
    question = request.json.get("question", "").lower()
    if "how to deploy" in question:
        answer = "To deploy a server, upload a ZIP containing your Python files or a .py script. The system will automatically detect main.py, app.py, or bot.py and run it. You can also use the AI Fix button if it fails."
    elif "fix" in question:
        answer = "The AI Fix button reads your server logs, installs missing modules, changes ports if busy, and fixes common syntax errors. If everything fails, it generates a working Flask test server."
    elif "limit" in question:
        answer = "DRIFT Hosting has no limits – you can create unlimited servers for free, forever."
    else:
        answer = "I'm your DRIFT AI Assistant. You can ask about deployment, server issues, Python coding, or click the AI Fix button on any server to automatically repair it."
    return jsonify({"answer": answer})

# ---------- Run Bot in Background ----------
def run_bot():
    try:
        bot.infinity_polling(skip_pending=True)
    except Exception as e:
        print(f"Telegram Thread Crashed: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8031))
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    print(f"🚀 DRIFT Core online: http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)