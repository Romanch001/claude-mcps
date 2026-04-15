"""
Arduino MCP Server — remote HTTP/SSE endpoint for claude.ai
Generates Arduino sketches, pin references, library suggestions, and circuit guidance.
No external API required.
"""
import os
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
import uvicorn

PORT = int(os.environ.get("PORT", 8000))
server = Server("arduino-mcp")
sse_transport = SseServerTransport("/messages/")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="generate_arduino_sketch",
            description=(
                "Generate a complete, ready-to-upload Arduino sketch (.ino) for a described project. "
                "Includes setup(), loop(), pin definitions, and comments."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": (
                            "Describe the project, e.g. 'blink LED on pin 13', "
                            "'read DHT22 temperature sensor and print to Serial', "
                            "'control servo with potentiometer', "
                            "'traffic light with 3 LEDs', 'ultrasonic distance sensor'."
                        )
                    },
                    "board": {
                        "type": "string",
                        "description": "Target board: 'Arduino Uno', 'Nano', 'Mega', 'ESP32', 'ESP8266'. Default: Uno.",
                        "default": "Arduino Uno"
                    }
                },
                "required": ["project"]
            }
        ),
        Tool(
            name="get_pin_reference",
            description="Get pin layout and specifications for an Arduino/microcontroller board.",
            inputSchema={
                "type": "object",
                "properties": {
                    "board": {
                        "type": "string",
                        "description": "Board name: 'Arduino Uno', 'Nano', 'Mega', 'ESP32', 'ESP8266', 'Pro Mini'."
                    }
                },
                "required": ["board"]
            }
        ),
        Tool(
            name="find_library",
            description="Find the best Arduino library for a sensor or component, with install instructions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "Component or sensor name, e.g. 'DHT22', 'MPU6050', 'SSD1306 OLED', 'HC-SR04', 'NeoPixel', 'BMP280', 'SD card'."
                    }
                },
                "required": ["component"]
            }
        ),
        Tool(
            name="explain_circuit",
            description="Explain how to wire a component to an Arduino: connections, resistors, voltage levels.",
            inputSchema={
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "Component to wire, e.g. 'LED', 'push button', 'DHT22', 'servo motor', 'I2C OLED', 'HC-SR04 ultrasonic', 'relay module'."
                    },
                    "board": {
                        "type": "string",
                        "description": "Target board. Default: Arduino Uno.",
                        "default": "Arduino Uno"
                    }
                },
                "required": ["component"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "generate_arduino_sketch":
        result = _gen_sketch(arguments.get("project", ""), arguments.get("board", "Arduino Uno"))
    elif name == "get_pin_reference":
        result = _pin_ref(arguments.get("board", "Arduino Uno"))
    elif name == "find_library":
        result = _find_lib(arguments.get("component", ""))
    elif name == "explain_circuit":
        result = _explain_circuit(arguments.get("component", ""), arguments.get("board", "Arduino Uno"))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


def _gen_sketch(project: str, board: str) -> str:
    p = project.lower()

    if "blink" in p or ("led" in p and "blink" in p):
        return f'''\
// Arduino Sketch: Blink LED
// Board: {board}
// Task: {project}

const int LED_PIN = 13;   // Built-in LED on most boards

void setup() {{
  pinMode(LED_PIN, OUTPUT);
  Serial.begin(9600);
  Serial.println("Blink sketch started");
}}

void loop() {{
  digitalWrite(LED_PIN, HIGH);   // Turn LED on
  delay(1000);                   // Wait 1 second
  digitalWrite(LED_PIN, LOW);    // Turn LED off
  delay(1000);                   // Wait 1 second
}}
'''

    if "dht" in p or "temperature" in p or "humidity" in p:
        return f'''\
// Arduino Sketch: DHT22 Temperature & Humidity Sensor
// Board: {board}
// Library: DHT sensor library by Adafruit (install via Library Manager)

#include <DHT.h>

#define DHTPIN 2        // Data pin connected to Arduino pin 2
#define DHTTYPE DHT22   // DHT22 (or DHT11 — change if needed)

DHT dht(DHTPIN, DHTTYPE);

void setup() {{
  Serial.begin(9600);
  dht.begin();
  Serial.println("DHT22 sensor ready");
}}

void loop() {{
  delay(2000);  // DHT22 sampling rate: once every 2 seconds

  float humidity    = dht.readHumidity();
  float tempC       = dht.readTemperature();       // Celsius
  float tempF       = dht.readTemperature(true);   // Fahrenheit

  if (isnan(humidity) || isnan(tempC)) {{
    Serial.println("ERROR: Failed to read from DHT sensor!");
    return;
  }}

  float heatIndexC = dht.computeHeatIndex(tempC, humidity, false);

  Serial.print("Humidity:    "); Serial.print(humidity);    Serial.println(" %");
  Serial.print("Temperature: "); Serial.print(tempC);       Serial.println(" °C");
  Serial.print("             "); Serial.print(tempF);       Serial.println(" °F");
  Serial.print("Heat index:  "); Serial.print(heatIndexC);  Serial.println(" °C");
  Serial.println("---");
}}
'''

    if "servo" in p or "potentiometer" in p:
        return f'''\
// Arduino Sketch: Control Servo with Potentiometer
// Board: {board}
// Task: {project}

#include <Servo.h>

Servo myServo;

const int POT_PIN   = A0;   // Potentiometer on analog pin A0
const int SERVO_PIN = 9;    // Servo signal wire on digital pin 9 (PWM)

void setup() {{
  myServo.attach(SERVO_PIN);
  Serial.begin(9600);
  Serial.println("Servo control ready");
}}

void loop() {{
  int potVal   = analogRead(POT_PIN);          // 0–1023
  int servoPos = map(potVal, 0, 1023, 0, 180); // Map to 0°–180°

  myServo.write(servoPos);

  Serial.print("Pot: "); Serial.print(potVal);
  Serial.print("  ->  Servo: "); Serial.print(servoPos); Serial.println("°");

  delay(15);   // Allow servo to reach position
}}
'''

    if "ultrasonic" in p or "hc-sr04" in p or "distance" in p:
        return f'''\
// Arduino Sketch: HC-SR04 Ultrasonic Distance Sensor
// Board: {board}
// Task: {project}

const int TRIG_PIN = 9;
const int ECHO_PIN = 10;

void setup() {{
  Serial.begin(9600);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  Serial.println("Ultrasonic sensor ready");
}}

float readDistanceCM() {{
  // Send 10µs pulse to TRIG
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // Measure ECHO pulse duration
  long duration = pulseIn(ECHO_PIN, HIGH, 30000); // 30 ms timeout
  if (duration == 0) return -1;   // No echo — object too far

  return duration * 0.0343 / 2;   // Speed of sound: 343 m/s
}}

void loop() {{
  float dist = readDistanceCM();

  if (dist < 0)
    Serial.println("Out of range (> 4 m)");
  else {{
    Serial.print("Distance: ");
    Serial.print(dist, 1);
    Serial.println(" cm");
  }}

  delay(200);
}}
'''

    if "traffic" in p or "3 led" in p or "three led" in p:
        return f'''\
// Arduino Sketch: Traffic Light with 3 LEDs
// Board: {board}
// Wiring: Red→pin 11, Yellow→pin 12, Green→pin 13 (each with 220Ω resistor to GND)

const int RED    = 11;
const int YELLOW = 12;
const int GREEN  = 13;

void allOff() {{
  digitalWrite(RED, LOW);
  digitalWrite(YELLOW, LOW);
  digitalWrite(GREEN, LOW);
}}

void setup() {{
  pinMode(RED,    OUTPUT);
  pinMode(YELLOW, OUTPUT);
  pinMode(GREEN,  OUTPUT);
  Serial.begin(9600);
  Serial.println("Traffic light started");
}}

void loop() {{
  // Green phase
  allOff(); digitalWrite(GREEN, HIGH);
  Serial.println("GREEN"); delay(5000);

  // Yellow phase
  allOff(); digitalWrite(YELLOW, HIGH);
  Serial.println("YELLOW"); delay(2000);

  // Red phase
  allOff(); digitalWrite(RED, HIGH);
  Serial.println("RED"); delay(5000);

  // Red + Yellow (UK-style get-ready phase)
  digitalWrite(YELLOW, HIGH);
  Serial.println("RED+YELLOW"); delay(1000);
}}
'''

    # Generic template
    return f'''\
// Arduino Sketch: {project}
// Board: {board}
// Generated template — fill in your logic below

// --- Pin Definitions ---
const int LED_PIN    = 13;
const int BUTTON_PIN = 2;
// Add more pins as needed...

// --- Global Variables ---
bool ledState = false;

void setup() {{
  // Initialize serial communication
  Serial.begin(9600);
  while (!Serial) {{ ; }}  // Wait for Serial port (Leonardo/Micro only)

  // Configure pins
  pinMode(LED_PIN,    OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);  // INPUT_PULLUP uses internal resistor

  Serial.println("Setup complete: {project}");
}}

void loop() {{
  // Read button (LOW when pressed with INPUT_PULLUP)
  bool buttonPressed = (digitalRead(BUTTON_PIN) == LOW);

  if (buttonPressed) {{
    ledState = !ledState;
    digitalWrite(LED_PIN, ledState ? HIGH : LOW);
    Serial.println(ledState ? "LED ON" : "LED OFF");
    delay(200);  // Debounce
  }}

  // Add your main loop logic here...
  delay(10);
}}
'''


def _pin_ref(board: str) -> str:
    b = board.lower()
    if "uno" in b:
        return """\
**Arduino Uno Pin Reference**

Digital I/O (14 pins, D0–D13):
- D0 (RX), D1 (TX) — Serial UART (avoid if using Serial monitor)
- D2, D3 — External interrupt capable (attachInterrupt)
- D3, D5, D6, D9, D10, D11 — PWM capable (~)
- D10 (SS), D11 (MOSI), D12 (MISO), D13 (SCK) — SPI bus
- D13 — Built-in LED

Analog Input (6 pins, A0–A5):
- A0–A5 can also be used as digital I/O (D14–D19)
- A4 (SDA), A5 (SCL) — I2C bus

Power:
- VIN: 7–12V input | 5V regulated out | 3.3V out (50 mA max)
- GND (multiple) | IOREF | RESET

Specs: 5V logic | 40 mA max per pin | ATmega328P | 16 MHz
"""
    if "nano" in b:
        return """\
**Arduino Nano Pin Reference**

Digital: D0–D13 (D0/RX, D1/TX avoid for Serial)
PWM: D3, D5, D6, D9, D10, D11
Analog: A0–A7 (A6 & A7 are analog-only — no digital mode)
I2C: A4=SDA, A5=SCL | SPI: D10–D13
Built-in LED: D13
Power: 5V pin | 3.3V pin (not 5V tolerant!) | VIN (7–12V)
USB: Mini-USB or Micro-USB depending on variant
"""
    if "mega" in b:
        return """\
**Arduino Mega 2560 Pin Reference**

Digital: D0–D53 (54 total)
PWM: D2–D13 and D44–D46 (15 pins total)
Analog: A0–A15 (16 pins)
UART: Serial (D0/1), Serial1 (D18/19), Serial2 (D16/17), Serial3 (D14/15)
I2C: D20 (SDA), D21 (SCL) | SPI: D50–D53
"""
    if "esp32" in b:
        return """\
**ESP32 Pin Reference** (30-pin DevKit)

GPIO: GPIO0–GPIO39 (not all exposed depending on module)
Special pins:
- GPIO0: Boot mode (don't drive LOW during startup)
- GPIO1 (TX0), GPIO3 (RX0): UART0 / USB Serial
- GPIO34–GPIO39: Input ONLY (no internal pull-up)
- GPIO6–GPIO11: Connected to internal flash (DO NOT USE)

ADC: GPIO32–GPIO39 (ADC1, works during WiFi); GPIO0–15 (ADC2, conflicts with WiFi)
DAC: GPIO25, GPIO26
Touch: GPIO2,4,12,13,14,15,27,32,33
I2C: GPIO21=SDA, GPIO22=SCL (configurable via Wire.begin(sda,scl))
SPI: GPIO23=MOSI, GPIO19=MISO, GPIO18=SCK, GPIO5=SS
PWM: any GPIO (LEDC peripheral)

Power: 3.3V logic! 5V tolerant? NO. Use level shifters for 5V sensors.
"""
    return f"**{board}**: see https://docs.arduino.cc/ for official pinout diagrams."


def _find_lib(component: str) -> str:
    c = component.lower()
    db = {
        "dht": ("DHT sensor library", "Adafruit", "#include <DHT.h>"),
        "mpu6050": ("MPU6050 by Electronic Cats", "electroniccats/mpu6050", "#include <MPU6050.h>"),
        "ssd1306": ("Adafruit SSD1306", "Adafruit", "#include <Adafruit_SSD1306.h>"),
        "neopixel": ("Adafruit NeoPixel", "Adafruit", "#include <Adafruit_NeoPixel.h>"),
        "bmp280": ("Adafruit BMP280 Library", "Adafruit", "#include <Adafruit_BMP280.h>"),
        "hc-sr04": ("NewPing", "Tim Eckel", "#include <NewPing.h>"),
        "servo": ("Servo (built-in)", "Arduino", "#include <Servo.h>"),
        "sd": ("SD (built-in)", "Arduino", "#include <SD.h>"),
        "wire": ("Wire / I2C (built-in)", "Arduino", "#include <Wire.h>"),
        "rf24": ("RF24", "TMRh20", "#include <RF24.h>"),
        "tm1637": ("TM1637Display", "Avishorp", "#include <TM1637Display.h>"),
    }
    for k, (lib, author, include) in db.items():
        if k in c:
            return f"""\
**Library for {component}:**
- Name: **{lib}**
- Author: {author}
- Include: `{include}`

Install: Arduino IDE → Tools → Manage Libraries → search "{lib.split()[0]}" → Install

Example:
```cpp
{include}
// See library examples in File → Examples → {lib.split()[0]}
```
"""
    return (
        f"**Library for {component}:**\n"
        "1. Open Arduino IDE → Tools → Manage Libraries\n"
        f"2. Search: `{component}`\n"
        "3. Install the library with the most stars/downloads\n"
        "4. Check File → Examples → [Library Name] for starter code\n\n"
        f"Also try: https://www.arduinolibraries.info/ — search for '{component}'"
    )


def _explain_circuit(component: str, board: str) -> str:
    c = component.lower()
    if "led" in c:
        return """\
**Wiring an LED to Arduino Uno**

Components needed: LED, 220Ω resistor

```
Arduino Pin 13 ──[220Ω]──►|── GND
                  resistor  LED  (anode → cathode)
```

Steps:
1. Connect 220Ω resistor between Arduino pin 13 and LED anode (+, longer leg)
2. Connect LED cathode (−, shorter leg) to GND
3. Value of resistor: R = (5V − 2V) / 0.02A = 150Ω minimum, 220Ω is safe

Code: `pinMode(13, OUTPUT); digitalWrite(13, HIGH);`
"""
    if "button" in c:
        return """\
**Wiring a Push Button to Arduino**

Use INPUT_PULLUP (no external resistor needed):

```
Arduino Pin 2 ──┬── Button ── GND
                │
            (internal
            pull-up 10kΩ)
```

Steps:
1. Connect one button leg to Arduino pin 2
2. Connect other leg to GND
3. In code: `pinMode(2, INPUT_PULLUP);`
4. Button reads LOW when pressed, HIGH when released

Alternative (external pull-down):
`Arduino 5V ── Button ── Pin 2 ──[10kΩ]── GND`
→ Reads HIGH when pressed.
"""
    if "dht" in c:
        return """\
**Wiring DHT22 to Arduino Uno**

DHT22 pinout (left to right, front facing):
1. VCC → Arduino 5V
2. DATA → Arduino Pin 2 + 10kΩ pull-up to 5V
3. NC (not connected)
4. GND → Arduino GND

```
5V ──[10kΩ]──┬── DHT22 Pin2 (DATA) ── Arduino D2
              │
             (pull-up resistor)
```

Library: DHT sensor library by Adafruit
`#include <DHT.h>   DHT dht(2, DHT22);`
"""
    if "servo" in c:
        return """\
**Wiring Servo Motor to Arduino**

Servo wire colors:
- Brown/Black: GND
- Red: 5V (VCC)
- Orange/Yellow/White: Signal → Arduino PWM pin (e.g. D9)

```
Arduino D9 ──── Servo Signal (orange)
Arduino 5V ──── Servo VCC   (red)
Arduino GND ─── Servo GND   (brown)
```

⚠️ For multiple servos or large servos, use external 5V power supply!
The Arduino 5V pin can only supply ~500 mA total.

Code: `#include <Servo.h>  Servo s; s.attach(9); s.write(90);`
"""
    if "i2c" in c or "oled" in c:
        return """\
**Wiring I2C OLED (SSD1306) to Arduino Uno**

Only 4 wires needed:
- VCC → Arduino 5V (or 3.3V)
- GND → Arduino GND
- SDA → Arduino A4
- SCL → Arduino A5

Default I2C address: 0x3C (sometimes 0x3D — check back of display)

```cpp
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

Adafruit_SSD1306 display(128, 64, &Wire, -1);
void setup() {
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  display.clearDisplay();
  display.println("Hello!"); display.display();
}
```
"""
    return (
        f"**Wiring {component} to {board}:**\n\n"
        f"1. Look up the {component} datasheet for pin definitions\n"
        "2. Common connections: VCC → 5V or 3.3V, GND → GND, Signal/Data → digital pin\n"
        "3. Check if pull-up/pull-down resistors are required\n"
        "4. Verify voltage compatibility (5V vs 3.3V logic)\n\n"
        f"Search: https://randomnerdtutorials.com/?s={component.replace(' ', '+')} for wiring diagrams"
    )


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "arduino-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
