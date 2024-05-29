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
    from xml.dom import minidom

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
    from xml.dom import minidom

    # Initialize global variables
damage_boost = False
heal = False
stop = False
run = False
mouse_btn = False
version = "1.4.0"

def create_default_config():
    root = ET.Element("config")
    comment = ET.Comment("You can adjust the percentage to switch to damage boost from 1 To 10. (you can type float numbers like '6.3')"
                         " The percentage is approximate and not precise, so you can experiment with the percentage you prefer!")
    root.append(comment)
    threshold = ET.SubElement(root, "mercy_bar_percentage")
    threshold.text = "10"
    comment = ET.Comment("this button for toggle pause code.")
    root.append(comment)
    pause_toggle_button = ET.SubElement(root, "pause_toggle_button")
    pause_toggle_button.text = "f2"
    comment = ET.Comment("this button for running mercy ai when you in game.")
    root.append(comment)
    comment = ET.Comment("visit https://pastebin.com/2qtsWPND for all keymap of keybaord.")
    root.append(comment)
    keybind_btn = ET.SubElement(root, "keybind_btn")
    keybind_btn.text = "left_mouse"
    heal_btn = ET.SubElement(root, "heal_btn")
    heal_btn.text = current_key('Press \033[0;33mHeal Button (Primary weapon)\033[0m: ','\033[0;33m')
    damage_boost_btn = ET.SubElement(root, "damage_boost_btn")
    damage_boost_btn.text = current_key('Press \033[036mDamage Boost Button (Secondary weapon)\033[0m: ','\033[036m')
    comment = ET.Comment("this is resolution of game,[2160,1440,1080,768]")
    root.append(comment)
    resolution = ET.SubElement(root, "resolution")
    resolution.text = display_resolutions()


    xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="    ")

    with open("config.xml", "w") as xml_file:
        xml_file.write(xml_str)


def read_config():
    tree = ET.parse("config.xml")
    root = tree.getroot()
    threshold = float(root.find("mercy_bar_percentage").text)
    pause_toggle_button = root.find("pause_toggle_button").text
    keybind_btn = root.find("keybind_btn").text
    damage_boost_btn = root.find("damage_boost_btn").text
    heal_btn = root.find("heal_btn").text
    selected_resolution = root.find("resolution").text

    return selected_resolution,heal_btn,damage_boost_btn,keybind_btn,threshold, pause_toggle_button


# Function to display available resolutions and get user input
def display_resolutions():
    os.system('cls')
    print("""
    \033[0;31mImportant\033[0;0m:
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
        print(f"{i}. \033[0;32m{res}\033[0;0m")
    # for skip enters of button choosing
    selected_resolution_index = int(input("Enter the number of your choice: "))
    selected_resolution = resolutions[selected_resolution_index - 1]
    os.system('cls')
    return selected_resolution


# Function to load information from an XML file based on the selected resolution
def load_info_from_xml(resolution):
    xml_path = os.path.join('data', resolution, "info.xml")

    if not os.path.exists(xml_path):
        print(f"Error: Missing info.xml file in {resolution} folder.")
        return None

    tree = ET.parse(xml_path)
    root = tree.getroot()

    res_element = root.find("res")
    bomb_res_element = root.find("bomb_res")
    if res_element and bomb_res_element:
        high = int(res_element.find("high").text)
        width = int(res_element.find("width").text)
        x = int(res_element.find("x").text)
        y = int(res_element.find("y").text)
        bomb_high = int(bomb_res_element.find("high").text)
        bomb_width = int(bomb_res_element.find("width").text)
        bomb_x = int(bomb_res_element.find("x").text)
        bomb_y = int(bomb_res_element.find("y").text)
        return bomb_high,bomb_width,bomb_x,bomb_y,high, width, x, y

    print(f"Error: Information for resolution {resolution} not found.")
    return None

def threshold_translator(value, from_min=1, from_max=10, to_min=0.2, to_max=0.99):
    scale = (to_max - to_min) / (from_max - from_min)
    return float(to_min + (value - from_min) * scale)


def keybind():
    global run, stop
    while True:
        time.sleep(0.06)
        if not stop:
            if keyboard.is_pressed(keybind_btn):
                run = True
            else:
                run = False


# Mouse click callback function to update global variables based on button press/release events
def on_click(x, y, button, pressed):
    global run, stop
    if not stop:
        if button == button.left and keybind_btn == 'left_mouse':
            if pressed:
                run = True
            else:
                run = False
        elif button == button.right and keybind_btn == 'right_mouse':
            if pressed:
                run = True
            else:
                run = False


# Function to start a listener thread for mouse clicks
def start_listener():
    with Listener(on_click=on_click) as listener:
        listener.join()


def current_key(text,color):
    text_button = """
    Now you have to add damage boost and heal buttons

    Add an extra button next to the mouse button in the game;

    \033[31mit should not be the mouse button itself.\033[0m

    Like: p or 4 or f4 or anything you want

    see github page for more information @Hexer-7
                """
    print(text_button)
    print(text)
    while True:
        event = keyboard.read_event()

        if event.event_type == keyboard.KEY_DOWN:
            if event.name == 'enter':
                input()
                os.system('cls')
                return current_key
            current_key = event.name
            sys.stdout.write('\r' + ' [ '+ f'{color}{current_key}\033[0m' +' ]'+' ' * 10 + '\r')
            sys.stdout.flush()



# Function to print the state of the Mercy AI (Running/Stopped)
def print_state():
    global stop
    message = '    Mercy AI is \033[31mStopped\033[0m' if stop else '    Mercy AI is \033[32mRunning\033[0m'
    sys.stdout.write('\r' + message + ' ' * (len(message) - len('    Mercy AI is stopped')) + '\r')
    sys.stdout.flush()

# Function to filter the input image based on a target color and apply a filter image
def filter_color(image, filter_image_path,add_blur=False):
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    lower = np.array([230, 230, 230])
    upper = np.array([255, 255, 255])
    mask = cv2.inRange(image, lower, upper)
    image = cv2.bitwise_and(image, image, mask=mask)

    image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, image = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY)

    filter_image = Image.open(filter_image_path).convert("RGBA")

    image_pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_GRAY2RGBA))
    combined_image = Image.alpha_composite(image_pil, filter_image)
    if add_blur:
        combined_image = np.array(combined_image)
        combined_image = cv2.GaussianBlur(combined_image, (41, 41), 0)
        combined_image = Image.fromarray(combined_image)

    return combined_image

def read_and_filter_images(health_bar_image_path,health_bar_filter_image_path,anas_bomb_image_path,anas_bomb_filter_image_path):
    health_bar_image = cv2.imread(health_bar_image_path)
    health_bar_image = cv2.resize(health_bar_image, (width, high))
    health_bar_image = filter_color(health_bar_image, health_bar_filter_image_path,add_blur=True)
    health_bar_image = np.array(health_bar_image)

    anas_bomb_image = cv2.imread(anas_bomb_image_path)
    anas_bomb_image = cv2.resize(anas_bomb_image, (bomb_width, bomb_high))
    anas_bomb_image = filter_color(anas_bomb_image, anas_bomb_filter_image_path)
    anas_bomb_image = np.array(anas_bomb_image)
    return health_bar_image,anas_bomb_image

def ana_boomb_check(bomb_region,anas_bomb_filter_image_path,anas_bomb_image):
    # Capture and process Ana's bomb
    with mss.mss() as sct:
        bomb_img = np.array(sct.grab(bomb_region))
        bomb_img = cv2.resize(bomb_img, (bomb_width, bomb_high))
        bomb_color_filtered_binary = filter_color(bomb_img, anas_bomb_filter_image_path)
        bomb_color_filtered_binary = np.array(bomb_color_filtered_binary)
        bomb_result = cv2.matchTemplate(bomb_color_filtered_binary, anas_bomb_image, cv2.TM_CCOEFF_NORMED)
        bomb_min_val, bomb_max_val, bomb_min_loc, bomb_max_loc = cv2.minMaxLoc(bomb_result)
        return bomb_max_val

def mercy_bar_check(region,health_bar_filter_image_path,health_bar_image):
    with mss.mss() as sct:
        global color_filtered_binary
        img = np.array(sct.grab(region))
        img = cv2.resize(img, (width, high))
        color_filtered_binary = filter_color(img, health_bar_filter_image_path,add_blur=True)
        color_filtered_binary = np.array(color_filtered_binary)
        result = cv2.matchTemplate(color_filtered_binary, health_bar_image, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
    return min_val

def capture_screen(health_bar_image_path, anas_bomb_image_path, health_bar_filter_image_path, anas_bomb_filter_image_path):
    global threshold, heal, damage_boost, run, stop

    health_bar_image,anas_bomb_image = read_and_filter_images(health_bar_image_path, health_bar_filter_image_path,
                                                              anas_bomb_image_path,anas_bomb_filter_image_path)

    last_action_time = time.time()
    region = {'left': x, 'top': y, 'width': width, 'height': high}
    bomb_region = {'left': bomb_x, 'top': bomb_y, 'width': bomb_width, 'height': bomb_high}

    while True:
        if not run:
            if damage_boost or heal:
                keyboard.release(damage_boost_btn)
                keyboard.release(heal_btn)
                damage_boost = False
                heal = False
            while not run:
                if keyboard.is_pressed(pause_toggle_button):
                    stop = not stop
                    print_state()
                    while keyboard.is_pressed(pause_toggle_button):
                        time.sleep(0.01)
                time.sleep(0.07)
        if stop:
            continue
        max_val = mercy_bar_check(region, health_bar_filter_image_path, health_bar_image)
        bomb_max_val = ana_boomb_check(bomb_region, anas_bomb_filter_image_path, anas_bomb_image)
        if max_val >= 0.999:
            max_val=1.0
        current_time = time.time()
        time_difference = current_time - last_action_time
        if time_difference >= 0.2:  # Add a delay of at least 0.2 seconds between decisions
            if (max_val >= threshold) or (bomb_max_val >= bomb_threshold):
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

    text = f"""

Please read the following carefully:
Mercy AI is completely free and open source. on github @Hexer-7

The code is an artificial intelligence that boosts damage if the allies' health is complete or close,
otherwise, it switches Mercy's weapon to increase health.

you can adjust damage boost switch percentage in config file.
version:{version}

\033[31mUse \033[0mwhite\033[31m color for health bar,shield,armor,overhealth (White only), Not [BLUE (FRIENDLY DEFAULT)], or etc.\033[0m

\033[32mIf you wanna reset config file, Delete it.\033[0m

If you have any feedback, contact me on my Instagram DM (_1_B) or @Hexer-7 on github.

\nPress ENTER to continue.
    """

    print(text)
    input()
    os.system('cls')
    if not os.path.isfile("config.xml"):
        create_default_config()

    # Read config values
    selected_resolution, heal_btn, damage_boost_btn, keybind_btn, threshold, pause_toggle_button = read_config()
    threshold = threshold_translator(threshold)

    bomb_threshold = float(0.77)
    info = load_info_from_xml(selected_resolution)
    if info:
        bomb_high, bomb_width, bomb_x, bomb_y, high, width, x, y = info

        os.system('cls')
        if keybind_btn == "left_mouse" or keybind_btn == "right_mouse":
            mouse_listener_thread = threading.Thread(target=start_listener)
            mouse_listener_thread.start()
        else:
            keybind_thread = threading.Thread(target=keybind)
            keybind_thread.start()
        if keybind_btn == "left_mouse":
            mouse_btn = "Mouse Left click"
        elif keybind_btn == "right_mouse":
            mouse_btn = "Mouse Right click"
        UI = """
                                    \033[31m    lj<                          \033[31m+jI\033[90m
                                   \033[31m     .?\\t~     \033[90m`~)xXUUXx)~'     \033[31m_f\-\033[90m
                                   \033[31m     '!I_t(  \033[90m:v*888&&&&888on"  \033[31m\\t+Il.\033[90m
                                  \033[31m      '>>;  \033[90mIX#8*oooooooooo*8*c,  \033[31mI>>\033[90m
                                 \033[31m       `l  \033[90mlUW&*o************o*8MX;  \033[31m!'\033[90m
                                          iLW&oo****************o*&WUl
                                        +0%8#####**************###*#8%L<
                                      ]Z8Mmdkbbbk&&****MM****&WbbbbkdmW80-
                                     CB&oo(      ~0**M%$$%M**Q<      |oo8BY
                                    ?rvq#MW0{,     xB@$@@$@Br     ;)ZWM#wuj_
                                    (/|\\vw*88Mhddh*B$$$$$$$$B*hddaW88*wu\|/)
                                    {rt/|\\umoo*&$$$$o*oooooo$$$@&*oomn||/tr}
                                     <\jt/|(u#@$$@$@(;>~~i;|$@@$$@*n(|/tj|>
                                       >|jjcLh%$$$$$*t    j#$$$$$%hLvjj|i
                                         >Ubq0Zk8$$@$$@~?$$$@$$8kOQwdXi
                                           -Jdw0Ob8$$Ll  >0$$&bO0wpY+
                                             +Ydqwmv; '{}. IzmwwpX~
                                               <zf^ :\\nrrn|, "rzi
                                                   '<~~++~~<. 

               """
        print(f"\033[90m{UI}\033[0m")
        print(f'    Now you can Run Mercy AI With Holding', (f'"{mouse_btn}"' if mouse_btn else f'"{keybind_btn}"'))
        print(f'    and you can toggle pause Mercy AI with "{pause_toggle_button}"\n')
        print_state()
        capture_screen(
            f'data/{selected_resolution}/white.png',
            f'data/{selected_resolution}/ana_bomb.png',
            f'data/{selected_resolution}/white_filter.png',
            f'data/{selected_resolution}/ana_bomb_filter.png'
        )
    else:
        print("Error: Resources not found!")
