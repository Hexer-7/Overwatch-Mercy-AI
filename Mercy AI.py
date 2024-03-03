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


    # Initialize global variables
damage_boost = False
heal = False
stop = False
run = False
    # Function to display available resolutions and get user input
def display_resolutions():
    print_gradual_text("""
    Important:
        you have to set the game on Fullscreen if have troubles with Mercy AI

Now you have to Choose the Resolution of your game 
    4K = 2160
    2K = 1440
    FHD = 1080
    HD+ = 768
    
    """,0.003)
    print_gradual_text("Choose a Resolution:")
    resolutions = ["2160", "1440", "1080", "768"]
    for i, res in enumerate(resolutions, start=1):
        print(f"{i}. {res}")

    selected_resolution_index = int(input("Enter the number of your choice: "))
    selected_resolution = resolutions[selected_resolution_index - 1]

    return selected_resolution

    # Function to load information from an XML file based on the selected resolution
def load_info_from_xml(resolution):
    xml_path = os.path.join('data',resolution, "info.xml")

    if not os.path.exists(xml_path):
        print_gradual_text(f"Error: Missing info.xml file in {resolution} folder.")
        return None

    tree = ET.parse(xml_path)
    root = tree.getroot()

    data_element = root.find("res")

    if data_element is not None:
        high = int(data_element.find("high").text)
        width = int(data_element.find("width").text)
        x = int(data_element.find("x").text)
        y = int(data_element.find("y").text)

        return high, width, x, y

    print_gradual_text(f"Error: Information for resolution {resolution} not found.")
    return None

    # Function to display available colors from an XML file and get user input
def display_colors(xml_path):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    colors = []
    print_gradual_text('''
The color we're talking about is the one on Mercy's bar in the middle of the screen, showing your teammate's Health.

(See a picture on my GitHub @Hexer-7)

Make sure the whole bar has the same color for shield, health, overhealth, and armor.
 Pick the color from the menu with the same name.

As said before, avoid using yellow or blue; they're not recommended and can be unstable.    
''')
    print_gradual_text("Choose the color of ally health bar:")
    for i, image in enumerate(root.findall(".//images/image"), start=1):
        color_name = image.get("name").split(".")[0]
        colors.append(color_name)
        print_gradual_text(f"{i}. {color_name}")

    selected_index = int(input("Enter the number of your choice: "))

    if 1 <= selected_index <= len(colors):
        selected_color = colors[selected_index - 1]
        color_node = root.find(f".//images/image[@name='{selected_color}.png']/RGB")
        if color_node is not None:
            selected_rgb = tuple(map(int, color_node.text[1:-1].split(',')))
            return selected_color, selected_rgb

    print_gradual_text("Invalid choice. Please enter a valid number.")
    return None, None

    # Mouse click callback function to update global variables based on button press/release events
def on_click(x, y, button, pressed):
    global run,stop

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

    # Function to print gradual text
def print_gradual_text(text, delay=0.003):
    for char in text:
        print(char, end='', flush=True)
        time.sleep(delay)
    print()

def print_state():
    global stop
    message = 'Mercy AI is \033[31mStopped\033[0m' if stop else 'Mercy AI is \033[32mRunning\033[0m'
    sys.stdout.write('\r' + message + ' ' * (len(message) - len('Mercy AI is stopped')) + '\r')
    sys.stdout.flush()

    # Function to filter the input image based on a target color
def filter_color(image):
    global Target_color
    # Ensure the image is in BGR format
    image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

    target_r, target_g, target_b = Target_color

    # Define a range around the target color
    color_range = 30

    lower = np.array([max(0, target_r - color_range),
                      max(0, target_g - color_range),
                      max(0, target_b - color_range)])
    upper = np.array([min(255, target_r + color_range),
                      min(255, target_g + color_range),
                      min(255, target_b + color_range)])

    mask = cv2.inRange(image, lower, upper)
    color_filtered = cv2.bitwise_and(image, image, mask=mask)
    color_filtered_binary = cv2.cvtColor(color_filtered, cv2.COLOR_BGR2GRAY)
    _, color_filtered_binary = cv2.threshold(color_filtered_binary, 1, 255, cv2.THRESH_BINARY)
    return color_filtered_binary

    # Function to continuously capture the screen, analyze images, and perform actions
def capture_screen(reference_image_path):
    global threshold,heal,damage_boost,run,stop
    reference_image = cv2.imread(reference_image_path, cv2.IMREAD_GRAYSCALE)
    reference_image = cv2.resize(reference_image, (width, high))

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
                        time.sleep(0.7)
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
    global high, width, x, y,Target_color
    mouse_listener_thread = threading.Thread(target=start_listener)
    mouse_listener_thread.start()
    text = """
    
Please read the following carefully:
Mercy AI is completely free and open source.

I do not recommend using similar colors to \033[33myellow\033[0m or \033[34mblue\033[0m,
due to similarity with the colors of Mercy's weapon.

The code is an artificial intelligence that boosts damage if the allies' health is complete or close,
otherwise, it switches Mercy's weapon to increase health.

The code is currently in beta, and there may be unintended errors.
If you have any feedback, contact me on my Instagram DM (_1_B) or TikTok (__hex).

\nPress ENTER to continue.
    """

    print_gradual_text(text,0.003)
    input()
    os.system('cls')
    selected_resolution = display_resolutions()
    info = load_info_from_xml(selected_resolution)

    if info:
        high, width, x, y = info
        os.system('cls')
        xml_path = os.path.join("data",selected_resolution, "info.xml")

        selected_color, Target_color = display_colors(xml_path)

        if selected_color and Target_color:
            os.system('cls')
            text_button = """
Now you have to add damage boost and heal buttons
            
Add an extra button next to the mouse button in the game;
            
it should not be the mouse button itself.

Like: p or num 1 or 4 or f4 or anything you want

see github page for more information @Hexer-7

            """
            print_gradual_text(text_button,0.003)
            damage_boost_btn = input('Enter Damage Boost Button: ')
            heal_btn = input('Enter Heal Button: ')
            threshold = 0.50
            os.system('cls')
            print_gradual_text(f'Now you can Run Mercy AI With Holding "Mouse Left click"')
            print_gradual_text(f'and you can pause Mercy AI with "F2"\n')
            print_state()
            capture_screen(f'data/{selected_resolution}/{selected_color}.png')

    else:
        print_gradual_text("Exiting due to missing info.xml.")


