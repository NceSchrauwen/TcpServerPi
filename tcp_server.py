import socket
import time
import board
import busio
from adafruit_pn532.i2c import PN532_I2C
import threading

# Hardcoded users
users = {
    "0437": {"username": "nina", "password": "4707"},
    "1111": {"username": "muub", "password": "2222"},
}

# Item database
item_database = {
    "0x466aca01": {"name": "The Downtown Lights", "price": 19.75},
    "0x238d5930": {"name": "The Way I Loved You", "price": 13.13},
    "0x540adea3": {"name": "Wicked Games", "price": 1.20}
}

# NFC setup
i2c = busio.I2C(board.SCL, board.SDA)
pn532_module = PN532_I2C(i2c, address=0x24, debug=False)
time.sleep(1)
pn532_module.SAM_configuration()
print("PN532 (NFC) is ready.")

# Global variable to set current state of NFC reader
nfc_active = True

# Socket setup
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("0.0.0.0", 12345))
server.listen(2) # Allow up to 2 connections
print("Waiting for connection from client...")

# Event to signal when to properly stop NFC reading/client message handling
stop_event = threading.Event()

# Function to be able to handle multiple clients
def client_thread(conn, addr):
    print(f"Connected to {addr}")
    try:
        if handle_login(conn):
            print("Login successful. Starting NFC loop.")
            # Start threads (non-daemon now)
            nfc_thread = threading.Thread(target=nfc_reader_loop, args=(conn,))
            client_messages = threading.Thread(target=handle_client_messages, args=(conn,))

            # Reset the stop event before starting threads
            stop_event.clear()
            
            nfc_thread.start()
            client_messages.start()

            # Wait until one thread finishes (e.g. disconnection or crash)
            nfc_thread.join()
            client_messages.join()
        else:
            print("Login failed.")
    except KeyboardInterrupt:
        print("Stopping client thread...")
    except Exception as e:
        print(f"Client thread error: {e}")
    finally:
        conn.close()
        print(f"Connection to {addr} closed.")

def handle_login(conn):
    try:
        data = conn.recv(1024).decode()
        parts = data.strip().split(',')
        if len(parts) == 3 and parts[0] == "LOGIN":
            user_id, password = parts[1], parts[2]
            user = users.get(user_id)
            if user and user["password"] == password:
                conn.send(f"LOGIN_SUCCESS,{user['username']}".encode())
                return True
        conn.send("LOGIN_FAILED".encode())
        return False
    except Exception as e:
        print(f"Login error: {e}")
        conn.send("LOGIN_FAILED".encode())
        return False

def nfc_reader_loop(conn):
    global nfc_active
    while True:
        try:
            if not nfc_active:
                time.sleep(0.2)
                continue

            # Check if connection is still valid
            if conn.fileno() == -1:
                print("NFC: Socket is closed. Exiting thread.")
                stop_event.set() # Stop the NFC thread if the connection is closed
                break

            uid = pn532_module.read_passive_target(timeout=1)
            if uid:
                uid_str = '0x' + ''.join([format(i, '02x') for i in uid])
                print(f"UID detected: {uid_str}")

                if uid_str in item_database:
                    item = item_database[uid_str]
                    message = f"Item found: {item['name']}, Price: ${item['price']:.2f}, UID: {uid_str}"
                else:
                    message = f"Item not found, UID: {uid_str}"

                print("Sending message: ", message)
                conn.send(message.encode())
            else:
                print("No NFC tag detected.")
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(0.1)

def handle_client_messages(conn):
    global nfc_active
    while True:
        try:
            # Check if connection is still valid
            if conn.fileno() == -1:
                print("Client message handler: Socket closed. Exiting thread.")
                stop_event.set() # Stop the handler thread if the connection is closed
                break

            data = conn.recv(1024).decode().strip()
            if not data:
                break

            if data == "NONSCAN_REQUEST":
                print("Received non-scan request. Pausing NFC.")
                nfc_active = False

            elif data == "NFC_RESTART":
                print("Received request to restart NFC scan. Restarting NFC.")
                nfc_active = True

            else:
                print(f"Received unrecognized message: {data}")

        except Exception as e:
            print(f"Error handling client message: {e}")
            break
        time.sleep(0.1)

# Accept connections in a main loop
try:
    while True:
        conn, addr = server.accept()
        thread = threading.Thread(target=client_thread, args=(conn, addr), daemon=True)
        thread.start()
except KeyboardInterrupt:
    print("Server stopped by user.")
finally:
    server.close()
    print("Server socket closed.")

