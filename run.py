import sys
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""
SERVER GODCLAN V2 - UNIVERSAL SECURE LAUNCHER
Runs the encrypted project in-memory with zero plain source code on disk.
- Auto-runs on Railway / VPS / Docker / Local PC.
- Decrypts and mounts core.enc purely in RAM.
"""
import os
import sys
import io
import zipfile
import flask
from jinja2 import DictLoader, ChoiceLoader
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC_V1 = b"SCARVAULT\x01"
MAGIC_V2 = b"SCARVAULT\x02"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT_FILE = os.path.join(PROJECT_DIR, "core.enc")
ENV_FILE = os.path.join(PROJECT_DIR, ".env")

def get_candidate_keys():
    keys = []
    # 1. System environment variable APP_KEY
    k = os.environ.get("APP_KEY", "").strip()
    if k:
        keys.append(k)

    # 2. Local .env file (APP_KEY or OWNER_PASSWORD)
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("APP_KEY="):
                        val = line.split("=", 1)[1].strip()
                        if val and val not in keys:
                            keys.append(val)
                    elif line.startswith("OWNER_PASSWORD="):
                        val = line.split("=", 1)[1].strip()
                        if val and val not in keys:
                            keys.append(val)
        except Exception:
            pass

    # 3. Automatic Stealth Key (Enables 100% headless auto-run on any machine)
    try:
        stealth = "".join(chr(b ^ 0x5a) for b in [10, 8, 19, 20, 25, 31, 26, 99, 109, 106, 98, 108, 109, 107])
        if stealth not in keys:
            keys.append(stealth)
    except Exception:
        pass

    return keys

def decrypt_vault(password: str):
    if not os.path.exists(VAULT_FILE):
        raise FileNotFoundError(f"Vault file not found: {VAULT_FILE}")

    with open(VAULT_FILE, "rb") as vf:
        data = vf.read()

    if data.startswith(MAGIC_V1):
        offset = len(MAGIC_V1)
        salt = data[offset:offset+16]
        nonce = data[offset+16:offset+28]
        ciphertext = data[offset+28:]
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000
        )
        key = kdf.derive(password.encode("utf-8"))
        aesgcm = AESGCM(key)
        raw_zip = aesgcm.decrypt(nonce, ciphertext, None)
    elif data.startswith(MAGIC_V2):
        num_slots = data[10]
        slot_len = 16 + 12 + 48
        recovered_key = None
        for i in range(num_slots):
            offset = 11 + i * slot_len
            s_data = data[offset:offset+slot_len]
            salt, nonce, ct = s_data[:16], s_data[16:28], s_data[28:]
            try:
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000
                )
                derived = kdf.derive(password.encode("utf-8"))
                recovered_key = AESGCM(derived).decrypt(nonce, ct, None)
                break
            except Exception:
                continue
        if not recovered_key:
            raise ValueError("Invalid password")
        payload_offset = 11 + num_slots * slot_len
        payload_nonce = data[payload_offset:payload_offset+12]
        payload_ct = data[payload_offset+12:]
        raw_zip = AESGCM(recovered_key).decrypt(payload_nonce, payload_ct, None)
    else:
        raise ValueError("Invalid vault magic header")

    zip_buf = io.BytesIO(raw_zip)
    bundle = {}
    with zipfile.ZipFile(zip_buf, "r") as zf:
        for name in zf.namelist():
            bundle[name] = zf.read(name)
    return bundle

def launch_app():
    print("=" * 65)
    print("       ⚡ SERVER GODCLAN V2 • SECURE ENCRYPTED RUNNER ⚡")
    print("=" * 65)

    # 1. If app.py exists plain on disk, run directly
    app_plain = os.path.join(PROJECT_DIR, "app.py")
    if os.path.exists(app_plain):
        print("[*] Detected app.py on disk. Running directly...")
        with open(app_plain, "r", encoding="utf-8") as f:
            code = f.read()
        global_scope = {"__name__": "__main__", "__file__": app_plain, "__builtins__": __builtins__}
        exec(compile(code, app_plain, "exec"), global_scope)
        return

    # 2. Encrypted Mode: Resolve candidate keys
    candidate_keys = get_candidate_keys()
    bundle = None

    for k in candidate_keys:
        try:
            bundle = decrypt_vault(k)
            if bundle:
                break
        except Exception:
            continue

    if not bundle:
        print("[*] Environment keys failed. Interactive key prompt:")
        try:
            user_key = input("🔑 Enter Decryption Password to Launch: ").strip()
        except EOFError:
            user_key = ""

        if user_key:
            try:
                bundle = decrypt_vault(user_key)
            except Exception as e:
                print("\n" + "=" * 65)
                print("❌ [!] DECRYPTION FAILED: Invalid Password or Corrupted Vault!")
                print(f"    Details: {e}")
                print("=" * 65 + "\n")
                sys.exit(1)
        else:
            print("[!] ERROR: Decryption password is required to launch.")
            sys.exit(1)

    print(f"[*] 🛡️ Decryption SUCCESS ({len(bundle)} protected modules loaded in RAM)!")

    # 3. Extract in-memory templates
    templates_dict = {}
    for path, content in bundle.items():
        if path.startswith("templates/"):
            tpl_name = path[len("templates/"):]
            try:
                decoded = content.decode("utf-8", errors="replace")
            except Exception:
                decoded = content.decode("latin-1")
            templates_dict[tpl_name] = decoded
            clean_base = os.path.basename(path)
            templates_dict[clean_base] = decoded
            templates_dict[path] = decoded

    # Hook Flask.__init__ so Jinja loads templates from memory
    orig_flask_init = flask.Flask.__init__
    def secure_flask_init(self, *args, **kwargs):
        orig_flask_init(self, *args, **kwargs)
        if self.jinja_loader:
            self.jinja_loader = ChoiceLoader([DictLoader(templates_dict), self.jinja_loader])
        else:
            self.jinja_loader = DictLoader(templates_dict)

    flask.Flask.__init__ = secure_flask_init

    # 4. Execute app.py in memory
    app_bytes = bundle.get("app.py")
    if not app_bytes:
        print("[!] ERROR: app.py not found inside encrypted vault!")
        sys.exit(1)

    code_str = app_bytes.decode("utf-8", errors="replace")
    fake_app_path = os.path.join(PROJECT_DIR, "app.py")
    compiled_code = compile(code_str, fake_app_path, "exec")

    global_scope = {
        "__name__": "__main__",
        "__file__": fake_app_path,
        "__builtins__": __builtins__
    }

    # Execute
    exec(compiled_code, global_scope)

if __name__ == "__main__":
    launch_app()
