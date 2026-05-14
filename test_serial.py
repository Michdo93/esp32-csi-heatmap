import serial

ser = serial.Serial('COM3', 921600, timeout=2)
print("Lese 20 Zeilen...")
for i in range(20):
    line = ser.readline().decode('utf-8', errors='ignore').strip()
    print(repr(line))
ser.close()