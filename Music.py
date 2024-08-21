from lib import st7789fbuf, mhconfig, mhoverlay, smartkeyboard, beeper
from font import vga2_16x32 as font
from font import vga1_8x16 as small_font  # Import a smaller font
import os, machine, time, math, framebuf, random, urequests
from machine import SDCard, Pin
from micropython import const
machine.freq(240000000)

"""
Music App
Version: 1

Description:
Gets wav files from a directory on the sd card called 'music'. It then lists this files to be selected and played.

Arrow keys to navigate/change songs, enter to play/pause.
"""

# Constants
_DISPLAY_HEIGHT = const(135)
_DISPLAY_WIDTH = const(240)
_CHAR_HEIGHT = const(32)
_ITEMS_PER_SCREEN = const(_DISPLAY_HEIGHT // _CHAR_HEIGHT)
_CHARS_PER_SCREEN = const(_DISPLAY_WIDTH // 16)
_SCROLL_TIME = const(5000)  # ms per one text scroll
_SCROLLBAR_WIDTH = const(3)
_SCROLLBAR_START_X = const(_DISPLAY_WIDTH - _SCROLLBAR_WIDTH)

# New constants for the smaller font
_SMALL_CHAR_HEIGHT = const(16)
_SMALL_CHAR_WIDTH = const(8)
_SMALL_CHARS_PER_SCREEN = const(_DISPLAY_WIDTH // _SMALL_CHAR_WIDTH)

# New constants for optimization
_UPDATE_INTERVAL = const(1000)  # Update display every 1 second
_PROGRESS_BAR_Y = const(100)  # Fixed Y position for progress bar
_PROGRESS_BAR_HEIGHT = const(10)
_PROGRESS_BAR_WIDTH = const(_DISPLAY_WIDTH - 20)

# Define pin constants
_SCK_PIN = const(41)
_WS_PIN = const(43)
_SD_PIN = const(42)

# Initialize hardware                                                                                                                                                                                                                                                                                                                                      
tft = st7789fbuf.ST7789(
    machine.SPI(
        1,baudrate=40000000,sck=machine.Pin(36),mosi=machine.Pin(35),miso=None),
    _DISPLAY_HEIGHT,
    _DISPLAY_WIDTH,
    reset=machine.Pin(33, machine.Pin.OUT),
    cs=machine.Pin(37, machine.Pin.OUT),
    dc=machine.Pin(34, machine.Pin.OUT),
    backlight=machine.Pin(38, machine.Pin.OUT),
    rotation=1,
    color_order=st7789fbuf.BGR
)

config = mhconfig.Config()
kb = smartkeyboard.KeyBoard(config=config)
overlay = mhoverlay.UI_Overlay(config, kb, display_fbuf=tft)
beep = beeper.Beeper()

sd = None
i2s = None

def mount_sd():
    global sd
    try:
        if sd is None:
            sd = SDCard(slot=2, sck=Pin(40), miso=Pin(39), mosi=Pin(14), cs=Pin(12))
        os.mount(sd, '/sd')
        print("SD card mounted successfully")
    except OSError as e:
        print("Could not mount SDCard:", str(e))
        overlay.error("SD Card Mount Error")

def read_wav_header(file):
    file.seek(0)
    riff = file.read(12)
    fmt = file.read(24)
    data_hdr = file.read(8)
    
    sample_rate = int.from_bytes(fmt[12:16], 'little')
    return sample_rate * 2

def setup_i2s(sample_rate):
    global i2s
    i2s = machine.I2S(0,
                      sck=machine.Pin(_SCK_PIN),
                      ws=machine.Pin(_WS_PIN),
                      sd=machine.Pin(_SD_PIN),
                      mode=machine.I2S.TX,
                      bits=16,
                      format=machine.I2S.MONO,
                      rate=sample_rate,
                      ibuf=1024)
        
def display_play_screen(selected_file, duration, current_position):
    # Clear the screen
    tft.fill(config["bg_color"])
    
    # Load and display the background image
    #load_and_display_image(selected_file) TODO - Get cover art background on play if possible.
    
    # Display song info
    parts = selected_file.rsplit('.', 1)[0].split(' - ')
    
    if len(parts) == 3:
        artist, album, song = parts
        info = [
            f"Artist: {artist}",
            f"Album: {album}",
            f"Song: {song}"
        ]
    else:
        info = [f"Playing: {selected_file}"]
    
    # Calculate starting y position to center the text vertically
    total_height = len(info) * _SMALL_CHAR_HEIGHT + 20  # Add extra space for progress bar
    start_y = (_DISPLAY_HEIGHT - total_height) // 2
    
    for idx, text in enumerate(info):
        y = start_y + idx * _SMALL_CHAR_HEIGHT
        x = 10  # Left align with a small margin
        
        # Truncate text if it's too long
        if len(text) > _SMALL_CHARS_PER_SCREEN:
            text = text[:_SMALL_CHARS_PER_SCREEN - 3] + "..."
        
        tft.bitmap_text(small_font, text, x, y, config.palette[4])
    
    # Draw progress bar
    bar_y = start_y + len(info) * _SMALL_CHAR_HEIGHT + 10
    bar_height = 10
    bar_width = _DISPLAY_WIDTH - 20  # Full width minus margins
    
    # Draw background of progress bar
    tft.fill_rect(10, bar_y, bar_width, bar_height, config.palette[2])
    
    # Draw filled portion of progress bar
    if duration > 0:
        fill_width = int((current_position / duration) * bar_width)
        tft.fill_rect(10, bar_y, fill_width, bar_height, config.palette[5])
    
    # Display time
    current_time = format_time(current_position)
    total_time = format_time(duration)
    time_text = f"{current_time} / {total_time}"
    time_x = (_DISPLAY_WIDTH - len(time_text) * _SMALL_CHAR_WIDTH) // 2
    time_y = bar_y + bar_height + 5
    tft.bitmap_text(small_font, time_text, time_x, time_y, config.palette[4])
    
    tft.show()

def format_time(seconds):
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"

class EasyWavMenu:
    def __init__(self, tft, config):
        self.tft = tft
        self.config = config
        self.main_items = ['Library', 'Shuffle', 'Download']
        self.cursor_index = 0
        self.view_index = 0
        self.current_view = 'main'  # 'main', 'library', or 'shuffle'
        self.wav_list_view = None
        self.items = self.main_items

    def draw(self):
        self.tft.fill(self.config["bg_color"])
        if self.current_view == 'main':
            self._draw_main_menu()
        elif self.current_view == 'library':
            self.wav_list_view.draw()
        self.tft.show()

    def _draw_main_menu(self):
        for idx, item in enumerate(self.main_items):
            color = self.config.palette[5] if idx == self.cursor_index else self.config.palette[4]
            self.tft.bitmap_text(font, item, 10, idx * _CHAR_HEIGHT, color)

    def select(self):
        if self.current_view == 'main':
            selected_item = self.main_items[self.cursor_index]
            if selected_item == 'Library':
                self.open_library()
            elif selected_item == 'Shuffle':
                return self.shuffle_play()
            elif selected_item == 'Download':
                return self.show_download_message()
        elif self.current_view == 'library':
            return "play"

    def show_download_message(self):
        overlay.draw_textbox("Download feature", _DISPLAY_WIDTH//2, _DISPLAY_HEIGHT//2 - 10)
        overlay.draw_textbox("coming soon!", _DISPLAY_WIDTH//2, _DISPLAY_HEIGHT//2 + 10)
        self.tft.show()
        time.sleep(2)  # Display the message for 2 seconds
        return "refresh"

    def open_library(self):
        print("Opening Library")
        if not self.wav_list_view:
            self.wav_list_view = WavListView(self.tft, self.config)
        self.wav_list_view.load_wav_files()
        self.current_view = 'library'
        self.cursor_index = self.wav_list_view.cursor_index
        self.items = self.wav_list_view.items

    def shuffle_play(self):
        print("Starting Shuffle Play")
        if not self.wav_list_view:
            self.wav_list_view = WavListView(self.tft, self.config)
            self.wav_list_view.load_wav_files()
        if self.wav_list_view.items:
            self.current_view = 'shuffle'
            random_song = random.choice(self.wav_list_view.items)
            print(f"Selected random song: {random_song}")
            return "play_shuffle", random_song
        else:
            print("No songs available for shuffle play")
            return None

    def up(self):
        if self.current_view == 'main':
            self.cursor_index = (self.cursor_index - 1) % len(self.main_items)
        elif self.current_view == 'library':
            self.wav_list_view.up()
            self.cursor_index = self.wav_list_view.cursor_index
            self.items = self.wav_list_view.items

    def down(self):
        if self.current_view == 'main':
            self.cursor_index = (self.cursor_index + 1) % len(self.main_items)
        elif self.current_view == 'library':
            self.wav_list_view.down()
            self.cursor_index = self.wav_list_view.cursor_index
            self.items = self.wav_list_view.items

    def back(self):
        if self.current_view in ['library', 'shuffle']:
            self.current_view = 'main'
            self.cursor_index = 0
            self.items = self.main_items
            return True
        return False

    def handle_input(self, key):
        if self.current_view == 'main':
            if key == ";":
                self.up()
                return "up"
            elif key == ".":
                self.down()
                return "down"
            elif key in ("ENT", "SPC"):
                return self.select()
        elif self.current_view == 'library':
            if key == ";":
                self.wav_list_view.up()
                self.cursor_index = self.wav_list_view.cursor_index
                self.items = self.wav_list_view.items
                return "up"
            elif key == ".":
                self.wav_list_view.down()
                self.cursor_index = self.wav_list_view.cursor_index
                self.items = self.wav_list_view.items
                return "down"
            elif key in ("ENT", "SPC"):
                return self.select()
        
        if key in ("`", "DEL", "ESC", "BKSP"):
            if self.back():
                return "back"
            else:
                return "exit"
        
        return None

class WavListView:
    def __init__(self, tft, config):
        self.tft = tft
        self.config = config
        self.items = []
        self.view_index = 0
        self.cursor_index = 0

    def load_wav_files(self):
        try:
            self.items = [f for f in os.listdir("/sd/music") if f.lower().endswith('.wav')]
            print("WAV files found:", self.items)
        except OSError as e:
            print("Error loading WAV files:", str(e))
            self.items = []

    def draw(self):
        if not self.items:
            self.tft.bitmap_text(font, "No WAV files found", 10, 10, self.config.palette[4])
        else:
            for idx in range(0, _ITEMS_PER_SCREEN):
                item_index = idx + self.view_index
                if item_index < len(self.items):
                    text = self.items[item_index]
                    is_selected = (item_index == self.cursor_index)
                    color = self.config.palette[5] if is_selected else self.config.palette[4]
                    
                    x = 10  # Default x position
                    
                    # Apply ping-pong scrolling only for the selected item if it's long
                    if is_selected and len(text) > _CHARS_PER_SCREEN:
                        scroll_distance = (len(text) - _CHARS_PER_SCREEN) * -16
                        x = int(ping_pong_ease(time.ticks_ms(), _SCROLL_TIME) * scroll_distance)
                    
                    self.tft.bitmap_text(font, text, x, idx * _CHAR_HEIGHT, color)

    def up(self):
        if self.items:
            self.cursor_index = (self.cursor_index - 1) % len(self.items)
            self.view_to_cursor()

    def down(self):
        if self.items:
            self.cursor_index = (self.cursor_index + 1) % len(self.items)
            self.view_to_cursor()

    def view_to_cursor(self):
        if self.cursor_index < self.view_index:
            self.view_index = self.cursor_index
        if self.cursor_index >= self.view_index + _ITEMS_PER_SCREEN:
            self.view_index = self.cursor_index - _ITEMS_PER_SCREEN + 1
            
    def back(self):
        return True

def ping_pong_ease(value, maximum):
    odd_pong = ((value // maximum) % 2 == 1)
    fac = ease_in_out_sine((value % maximum) / maximum)
    return 1 - fac if odd_pong else fac

def ease_in_out_sine(x):
    return -(math.cos(math.pi * x) - 1) / 2

def play_sound(notes, time_ms=30):
    if config['ui_sound']:
        beep.play(notes, time_ms, config['volume'])

def main_loop():
    mount_sd()
    view = EasyWavMenu(tft, config)
    
    while True:
        view.draw()
        
        new_keys = kb.get_new_keys()
        for key in new_keys:
            action = view.handle_input(key)
            
            if action == "up":
                play_sound(("G3","B3"), 30)
            elif action == "down":
                play_sound(("D3","B3"), 30)
            elif action == "select":
                play_sound(("G3","B3","D3"), 30)
            elif action == "refresh":
                # Refresh the WAV file list
                if view.wav_list_view:
                    view.wav_list_view.load_wav_files() 
            if action == "play" or (isinstance(action, tuple) and action[0] == "play_shuffle"):
                if view.current_view in ['library', 'shuffle'] and view.items:
                    if isinstance(action, tuple) and action[0] == "play_shuffle":
                        selected_file = action[1]
                    else:
                        selected_file = view.items[view.cursor_index]
                    try:
                        with open(f"/sd/music/{selected_file}", 'rb') as file:
                            sample_rate = read_wav_header(file)
                            setup_i2s(sample_rate)
                            
                            # Get file size for duration calculation
                            file.seek(0, 2)
                            file_size = file.tell()
                            file.seek(44)  # Skip WAV header
                            
                            # Calculate total duration (approximate)
                            duration = (file_size - 44) / (sample_rate * 2)  # 16-bit mono
                            
                            start_time = time.ticks_ms()
                            while True:
                                data = file.read(1024)
                                if not data:
                                    break
                                i2s.write(data)
                                
                                # Calculate current position
                                current_position = (time.ticks_ms() - start_time) / 1000
                                
                                # Update display every 1000ms
                                if time.ticks_ms() % 1000 == 0:
                                   display_play_screen(selected_file, duration, current_position)
                                
                                if kb.get_new_keys():  # Check for key press to stop playback
                                    break
                            
                            i2s.deinit()
                    except Exception as e:
                        print(f"Error playing file: {str(e)}")
                        overlay.error(f"Playback Error: {str(e)[:20]}")
                        
            elif action == "back":
                play_sound(("D3","B3","G3"), 30)
            elif action == "exit":
                return  # Exit the app
        
        time.sleep_ms(10)

try:
    main_loop()
except Exception as e:
    print("Error:", str(e))
    overlay.error(str(e))
finally:
    if sd:
        os.umount('/sd')
        print("SD card unmounted")




