import socket
import time
import board
import busio
from adafruit_pn532.i2c import PN532_I2C
import threading

admin_users = {
    "1234": {"username": "admin", "password": "0000"},
    "0000": {"username": "admin", "password": "0000"},
}

desktop_users = {
    "0437": {"username": "nina", "password": "4707"},
    "1999": {"username": "maud", "password": "1999"},
}

# Item database
item_database = {
    "0x466aca01": {"name": "The Downtown Lights", "price": "€19.75"},
    # "0x238d5930": {"name": "Fresh Out The Slammer", "price": "€13.13"},
    "0x540adea3": {"name": "Wicked Games", "price": "€6.66"}
}

# NFC setup
i2c = busio.I2C(board.SCL, board.SDA)
pn532_module = PN532_I2C(i2c, address=0x24, debug=False)
time.sleep(1)
pn532_module.SAM_configuration()
print("PN532 (NFC) is ready.")

# Global variable to set current state of NFC reader
nfc_active = True

# Global variable to store if a non-scan request was sent
# This is used to prevent providing multiple non-scan requests in a row
last_nonscan_sent = False

# Socket setup
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("0.0.0.0", 12345))
server.listen(2) # Allow up to 2 connections
print("Waiting for connection from client...")

# Global reference to the Android/Desktop client socket 
connections = {
    "android": None,
    "desktop": None
}

# Event to signal when to properly stop NFC reading/client message handling
stop_event = threading.Event()

# Function to be able to handle multiple clients
def client_thread(conn, addr):
    print(f"Connected to {addr}")
    global android_conn, nfc_active
    login_success, returned_user_id = handle_login(conn)
    try:
        if login_success:
            print(f"Login successful. User ID: {returned_user_id}")

            # If this is the Android app, store the connection
            if returned_user_id in admin_users:  # or whatever ID Android uses
                connections["android"] = conn
                print("Stored Android connection.")
            elif returned_user_id in desktop_users:  # or whatever ID Desktop uses
                connections["desktop"] = conn
                print("Stored Desktop connection")
            else:
                print("Unknown user ID. Closing connection.")
                conn.close()
                return

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
        
        if conn == connections["android"]:
            connections["android"] = None
            
        if conn == connections["desktop"]:
            connections["desktop"] = None

def handle_login(conn):
    try:
        data = conn.recv(1024).decode()
        parts = data.strip().split(',')
        if len(parts) == 3 and parts[0] == "LOGIN":
            user_id, password = parts[1], parts[2]
            # user = users.get(user_id)

            if user_id in admin_users:
                user = admin_users[user_id]
            elif user_id in desktop_users:
                user = desktop_users[user_id]
            else:
                print(f"Unknown user ID: {user_id}")
                conn.send("LOGIN_FAILED\n".encode())
                return False, None
            
            if user and user["password"] == password:
                conn.send(f"LOGIN_SUCCESS,{user['username']}\n".encode())
                print(f"User: {user['username']} with user_id: {user_id} logged in successfully.")
                return True, user_id
        conn.send("LOGIN_FAILED\n".encode())
        return False, None
    except Exception as e:
        print(f"Login error: {e}")
        conn.send("LOGIN_FAILED\n".encode())
        return False, None

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
                    price = item['price'] 
                    price_str = float(price.replace('€', ''))
                    message = f"{item['name']}, Price: €{price_str:.2f}, UID: {uid_str}"
                else:
                    message = f"UID not found in db, UID: {uid_str}"

                print("Sending message: ", message)
                conn.send(message.encode())
            else:
                print("No NFC tag detected.")
        except BrokenPipeError:
            print("Broken pipe error. Connection might be closed.")
            break
        except Exception as e:
            print(f"NFC reader error: {e}")
            break
        time.sleep(1.5) # Adjust the sleep time as needed

def handle_client_messages(conn):
    global nfc_active, user_id, android_conn, desktop_conn, last_nonscan_sent
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
                # Keep track of the last non-scan request sent
                last_nonscan_sent = True
                print("Sending non-scan request to Android.")

                try:
                    if connections["android"]:
                        connections["android"].sendall("NONSCAN_REQUEST\n".encode())
                        print("Forwarded non-scan request to Android.")
                    else:
                        print("No Android connection to forward request.")
                except Exception as e:
                    print(f"Error sending to Android: {e}")

            elif data == "NFC_RESTART":
                print("Received request to restart NFC scan. Restarting NFC.")
                nfc_active = True

            elif data == "FETCH_LATEST":
                if last_nonscan_sent:
                    try:
                        conn.sendall("NONSCAN_REQUEST\n".encode())
                        # Reset the last non-scan request flag, as we are resending it
                        # This is to prevent sending multiple non-scan requests in a row without a new request
                        last_nonscan_sent = False
                        print("Sent last non-scan request again upon fetch request from android.")
                    except Exception as e:
                        print(f"Failed to resend non-scan request: {e}")
                else:
                    print("No non-scan request to resend.")

            elif data.strip().upper() == "LOGOUT":
                print("Received logout request. Closing connection.")
                break # This will exit the loop and close the connection

            elif data.strip().upper() == "PING":
                print("Received ping request. Sending pong.")
                conn.sendall("PONG\n".encode())
                
            elif data.strip().upper() in ["APPROVED", "DENIED"]:
                print(f"Non-scan approval request from android is: {data}")
                
                if connections["desktop"]:
                    connections["desktop"].sendall(f"{data}\n".encode())
                    print("Response forwarded to desktop")
                else:
                    print("No desktop connection established, unable to send response")
            else:
                print(f"Received unrecognized message: {data}")
        except BrokenPipeError:
            print("Broken pipe error. Connection might be closed.")
            break
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
