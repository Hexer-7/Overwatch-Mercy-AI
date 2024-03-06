# Import necessary libraries
try:
    import sys
    import time
    import cv2
    import mss
    import numpy as np
    import keyboard
    from pynput.mouse import Listener
    import threading
    import os
    import xml.etree.ElementTree as ET
    import subprocess
    from PIL import Image
except ImportError as e:
    import subprocess

    # Install required packages using subprocess if not found
    subprocess.call(['pip', 'install', 'mss'])
    subprocess.call(['pip', 'install', 'opencv-python'])
    subprocess.call(['pip', 'install', 'keyboard'])
    subprocess.call(['pip', 'install', 'pynput'])
    subprocess.call(['pip', 'install', 'numpy'])

    import time
    import cv2
    import mss
    import numpy as np
    import keyboard
    from pynput.mouse import Listener
    import threading
    import os
    import xml.etree.ElementTree as ET
    from PIL import Image

    # Initialize global variables
damage_boost = False
heal = False
stop = False
run = False
locations_array = []


# Function to display available resolutions and get user input
def display_resolutions():
    print("""
    Important:
        you have to set the game on Fullscreen if have troubles with Mercy AI

Now you have to Choose the Resolution of your game 
    4K = 2160
    2K = 1440
    FHD = 1080
    HD+ = 768

    """)
    print("Choose a Resolution:")
    resolutions = ["2160", "1440", "1080", "768"]
    for i, res in enumerate(resolutions, start=1):
        print(f"{i}. {res}")

    selected_resolution_index = int(input("Enter the number of your choice: "))
    selected_resolution = resolutions[selected_resolution_index - 1]

    return selected_resolution


# Function to load information from an XML file based on the selected resolution
def load_info_from_xml(resolution):
    xml_path = os.path.join('data', resolution, "info.xml")

    if not os.path.exists(xml_path):
        print(f"Error: Missing info.xml file in {resolution} folder.")
        return None

    tree = ET.parse(xml_path)
    root = tree.getroot()

    data_element = root.find("res")
    locations_element = root.find("locations")  # New element for locations

    if data_element is not None:
        high = int(data_element.find("high").text)
        width = int(data_element.find("width").text)
        x = int(data_element.find("x").text)
        y = int(data_element.find("y").text)

        # Read locations and add them to the array
        for coord_element in locations_element.findall("coord"):
            x_coord = int(coord_element.get("x"))
            y_coord = int(coord_element.get("y"))
            locations_array.append((x_coord, y_coord))

        return high, width, x, y

    print(f"Error: Information for resolution {resolution} not found.")
    return None


# Mouse click callback function to update global variables based on button press/release events
def on_click(x, y, button, pressed):
    global run, stop

    if not stop:
        if button == button.left:
            if pressed:
                run = True
            else:
                run = False


# Function to start a listener thread for mouse clicks
def start_listener():
    with Listener(on_click=on_click) as listener:
        listener.join()


# Function to print the state of the Mercy AI (Running/Stopped)
def print_state():
    global stop
    message = 'Mercy AI is \033[31mStopped\033[0m' if stop else 'Mercy AI is \033[32mRunning\033[0m'
    sys.stdout.write('\r' + message + ' ' * (len(message) - len('Mercy AI is stopped')) + '\r')
    sys.stdout.flush()


# Function to filter the input image based on a target color
def filter_color(image):
    global locations_array
    # Ensure the image is in BGR format
    image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

    lower = np.array([240, 240, 240])
    upper = np.array([255, 255, 255])

    mask = cv2.inRange(image, lower, upper)
    image = cv2.bitwise_and(image, image, mask=mask)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, image = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY)
    image_mask = create_shaded_image(locations_array)

    # Convert the shaded image to a binary mask
    image_mask = cv2.cvtColor(np.array(image_mask), cv2.COLOR_RGBA2GRAY)

    _, image_mask = cv2.threshold(image_mask, 0, 250, cv2.THRESH_BINARY)
    image = cv2.bitwise_and(image, image, mask=image_mask)
    return image


# Function to create a shaded image based on a list of coordinates
def create_shaded_image(locations_array):
    image = Image.new('RGBA', (13, 13), (255, 255, 255, 0))
    shaded_pixels = locations_array

    for pixel in shaded_pixels:
        image.putpixel((pixel[0], pixel[1]), (0, 0, 0, 0))

    return image

# Function to continuously capture the screen, analyze images, and perform actions

def capture_screen(reference_image_path):
    global threshold, heal, damage_boost, run, stop
    reference_image = cv2.imread(reference_image_path)
    reference_image = cv2.resize(reference_image, (width, high))
    reference_image = filter_color(reference_image)

    reference_image = reference_image.astype(np.uint8)
    last_action_time = time.time()

    with mss.mss() as sct:

        region = {'left': x, 'top': y, 'width': width, 'height': high}

        while True:

            if not run:
                if damage_boost or heal:
                    keyboard.release(damage_boost_btn)
                    keyboard.release(heal_btn)
                    damage_boost = False
                    heal = False
                while not run:
                    if keyboard.is_pressed('f2'):
                        stop = not stop
                        print_state()
                        while keyboard.is_pressed('f2'):
                            time.sleep(0.01)
                    time.sleep(0.07)
            if stop:
                continue
            img = np.array(sct.grab(region))
            img = cv2.resize(img, (width, high))

            color_filtered_binary1 = filter_color(img)
            color_filtered_binary = color_filtered_binary1.astype(np.uint8)
            result = cv2.matchTemplate(color_filtered_binary, reference_image, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            current_time = time.time()
            time_difference = current_time - last_action_time

            if time_difference >= 0.2:  # Add a delay of at least 0.2 seconds between decisions
                if max_val > threshold:
                    if not damage_boost:
                        keyboard.press(damage_boost_btn)
                        damage_boost = True
                        last_action_time = current_time
                else:
                    if damage_boost:
                        keyboard.release(damage_boost_btn)
                        damage_boost = False

                    if not heal:
                        keyboard.press(heal_btn)
                        heal = True
                        last_action_time = current_time

# Main script

if __name__ == "__main__":
    global high, width, x, y
    mouse_listener_thread = threading.Thread(target=start_listener)
    mouse_listener_thread.start()
    text = """
    
Please read the following carefully:
Mercy AI is completely free and open source.

The code is an artificial intelligence that boosts damage if the allies' health is complete or close,
otherwise, it switches Mercy's weapon to increase health.

This version 1.2.1 white edition is most stable version of Mercy AI.
\033[31mUse \033[0mwhite\033[31m color for health bar only, Not [BLUE (FRIENDLY DEFAULT)], or etc.\033[0m

If you have any feedback, contact me on my Instagram DM (_1_B).

\nPress ENTER to continue.
    """

    print(text)
    input()
    os.system('cls')
    selected_resolution = display_resolutions()
    info = load_info_from_xml(selected_resolution)

    if info:
        high, width, x, y = info
        os.system('cls')
        text_button = """
Now you have to add damage boost and heal buttons
            
Add an extra button next to the mouse button in the game;
            
\033[31mit should not be the mouse button itself.\033[0m

Like: p or num 1 or 4 or f4 or anything you want

see github page for more information @Hexer-7
            """
        print(text_button)
        damage_boost_btn = input('Enter Damage Boost Button: ')
        heal_btn = input('Enter Heal Button: ')
        threshold = 0.40
        os.system('cls')
        print(f'Now you can Run Mercy AI With Holding "Mouse Left click"')
        print(f'and you can pause Mercy AI with "F2"\n')
        print_state()
        capture_screen(f'data/{selected_resolution}/white.png')

    else:
        print("Exiting due to missing info.xml.")
