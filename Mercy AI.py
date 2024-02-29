    # Import necessary libraries
try:
    import time
    import cv2
    import mss
    import numpy as np
    import keyboard
    from pynput.mouse import Listener
    import threading
    import pyautogui
    import os
    import xml.etree.ElementTree as ET
except ImportError as e:
    import subprocess

    # Install required packages using subprocess if not found
    subprocess.call(['pip', 'install', 'mss'])
    subprocess.call(['pip', 'install', 'opencv-python'])
    subprocess.call(['pip', 'install', 'keyboard'])
    subprocess.call(['pip', 'install', 'pyautogui'])
    subprocess.call(['pip', 'install', 'pynput'])
    subprocess.call(['pip', 'install', 'numpy'])

    import time
    import cv2
    import mss
    import numpy as np
    import keyboard
    from pynput.mouse import Listener
    import threading
    import pyautogui
    import os
    import xml.etree.ElementTree as ET

    # Initialize global variables
right = False
left = False

    # Function to display available resolutions and get user input
def display_resolutions():
    print("Choose a resolution:")
    resolutions = ["2160", "1440", "1080", "768"]
    for i, res in enumerate(resolutions, start=1):
        print(f"{i}. {res}")

    selected_resolution_index = int(input("Enter the number of your choice: "))
    selected_resolution = resolutions[selected_resolution_index - 1]

    return selected_resolution

    # Function to load information from an XML file based on the selected resolution
def load_info_from_xml(resolution):
    xml_path = os.path.join(resolution, "info.xml")

    if not os.path.exists(xml_path):
        print(f"Error: Missing info.xml file in {resolution} folder.")
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

    print(f"Error: Information for resolution {resolution} not found.")
    return None

    # Function to display available colors from an XML file and get user input
def display_colors(xml_path):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    colors = []
    print("Choose a color:")
    for i, image in enumerate(root.findall(".//images/image"), start=1):
        color_name = image.get("name").split(".")[0]
        colors.append(color_name)
        print(f"{i}. {color_name}")

    selected_index = int(input("Enter the number of your choice: "))

    if 1 <= selected_index <= len(colors):
        selected_color = colors[selected_index - 1]
        color_node = root.find(f".//images/image[@name='{selected_color}.png']/RGB")
        if color_node is not None:
            selected_rgb = tuple(map(int, color_node.text[1:-1].split(',')))
            return selected_color, selected_rgb

    print("Invalid choice. Please enter a valid number.")
    return None, None

    # Mouse click callback function to update global variables based on button press/release events
def on_click(x, y, button, pressed):
    global left,right
    if button == button.left:
        if pressed:
            left = True
        else:
            left = False
    if button == button.right:
        if pressed:
            right = True
        else:
            right = False

    # Function to start a listener thread for mouse clicks
def start_listener():
    with Listener(on_click=on_click) as listener:
        listener.join()

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
    global run,threshold,left,right
    reference_image = cv2.imread(reference_image_path, cv2.IMREAD_GRAYSCALE)
    reference_image = cv2.resize(reference_image, (width, high))

    reference_image = reference_image.astype(np.uint8)

    with mss.mss() as sct:


        region = {'left': x, 'top': y, 'width': width, 'height': high}
        run = False
        print("\n")
        print(f"The Bot is paused, Run it with toggle button '{toggle}'")
        while True:


            if keyboard.is_pressed(toggle):

                pyautogui.mouseUp(button='right')
                pyautogui.mouseUp(button='left')
                right = False
                left = False
                if run:
                    run = False
                else:
                    run = True
                time.sleep(0.1)
            if not run:
                time.sleep(0.05)
                continue
            while not keyboard.is_pressed(run_btn):

                if right or left:
                    pyautogui.mouseUp(button='right')
                    pyautogui.mouseUp(button='left')
                    right = False
                    left = False
                time.sleep(0.1)
            img = np.array(sct.grab(region))
            img = cv2.resize(img, (width, high))

            color_filtered_binary1 = filter_color(img)
            color_filtered_binary = color_filtered_binary1.astype(np.uint8)
            result = cv2.matchTemplate(color_filtered_binary, reference_image, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            if max_val > threshold:
                if not right:
                    pyautogui.mouseDown(button='right')
                    time.sleep(0.01)
                    right = True

            else:
                if right:
                    pyautogui.mouseUp(button='right')
                    time.sleep(0.01)
                    right = False

                if not left:
                    pyautogui.mouseDown(button='left')
                    time.sleep(0.01)
                    left = True

    # Main script
if __name__ == "__main__":
    global high, width, x, y,Target_color
    mouse_listener_thread = threading.Thread(target=start_listener)
    mouse_listener_thread.start()
    text = """
    Please read the following carefully:
    Firstly, the bot is completely free and open source.
    
    I do not recommend using similar colors to \033[33myellow\033[0m or \033[34mblue\033[0m,
    due to similarity with the colors of Mercy's weapon.
    
    The code is an artificial intelligence that boosts damage if the allies' health is complete or close,
    otherwise, it switches Mercy's weapon to increase health.
    
    The code is currently in beta, and there may be unintended errors.
    If you have any feedback, contact me on my Instagram private messages (_1_B) or TikTok (__hex).
    """

    input(f'{text}\nPress ENTER to continue.')
    os.system('cls')
    selected_resolution = display_resolutions()
    info = load_info_from_xml(selected_resolution)

    if info:
        high, width, x, y = info
        os.system('cls')
        xml_path = os.path.join(selected_resolution, "info.xml")
        selected_color, Target_color = display_colors(xml_path)

        if selected_color and Target_color:
            os.system('cls')
            run_btn = input("Type the Button you want to Run the Bot while pressing it(Like: b, 2,num 1 or f2 (Not Mouse Button!):")
            print("\n")
            toggle = input("Type the Button you want to toggle pausing the Bot (Like: b, 2,num 1 or f2 (Not Mouse Button!):")
            threshold = 0.40
            os.system('cls')
            print(f"Press '{toggle}' to toggle pause the Bot")
            print("\n")
            print(f"Press '{run_btn}' to pause the Bot while you pressing '{run_btn}'")
            mouse_listener_thread = threading.Thread(target=start_listener)
            mouse_listener_thread.start()
            capture_screen(f'{selected_resolution}/{selected_color}.png')

    else:
        print("Exiting due to missing info.xml.")


