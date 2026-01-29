import os
import sys
import queue
import time
import threading
import sounddevice as sd
from vosk import Model, KaldiRecognizer
import json
import pyautogui

def _base_path():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

# при сборке в exe (PyInstaller) ресурсы лежат рядом с exe или в _MEIPASS
BASE_DIR = _base_path()

try:
    import mouse
    MOUSE_AVAILABLE = True
except ImportError:
    MOUSE_AVAILABLE = False
    print("ВНИМАНИЕ: mouse не установлен. Установите: pip install mouse")

try:
    import win32api
    import win32con
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False
    print("ВНИМАНИЕ: pywin32 не установлен. Для лучшей работы в играх установите: pip install pywin32")

try:
    import dxcam
    DXCAM_AVAILABLE = True
except ImportError:
    DXCAM_AVAILABLE = False
    print("ВНИМАНИЕ: dxcam не установлен. Фоновая детекция не будет работать.")

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("ВНИМАНИЕ: cv2/numpy не установлены. Фоновая детекция может не работать.")

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    print("ВНИМАНИЕ: ultralytics не установлен. Фоновая детекция не будет работать.")

try:
    import winsound
    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False

MODEL_PATH = os.path.join(BASE_DIR, "model")
SAMPLE_RATE = 16000
WORDS_SPEC = '["влево вправо перед назад стоп снаряд один два три снаряжение активировать деактивировать снайперский выстрел отставить цель отключить пилота включить пилота четыре пять шесть семь восемь девять десять одиннадцать двенадцать час часа часов огонь налево направо прямо вперед поднять опустить дуло ствол", "[unk]"]'
CONTROLS = ["w", "a", "s", "d"]
# «Влево»/«вправо»: зажать клавишу на это время (сек), затем отпустить (не удержание до «стоп»)
turn_key_hold_duration = 1.0

KRONOS_MODEL_PATH = os.path.join(BASE_DIR, "kronos_models", "best.pt")
kronos_model = None
kronos_camera = None
screen_x, screen_y = pyautogui.size()
center_x, center_y = screen_x // 2, screen_y // 2
fov = 300  # Радиус поиска цели (пиксели)
confidence_threshold = 0.5
auto_aim_enabled = True  # Флаг включения фоновой детекции
auto_aim_thread = None  # Поток фоновой детекции
last_right_click_time = 0  # Время последнего клика правой кнопкой
right_click_cooldown = 0.1  # Задержка между кликами (секунды)
right_click_locked = False  # Флаг блокировки автоматического нажатия ПКМ
# В режиме «Зафиксирован на цели»: при высокой уверенности — два клика ПКМ с интервалом 0.1 с
last_rmb_double_click_time = 0.0
rmb_double_click_cooldown = 1.0   # сек между «двойными кликами» ПКМ
rmb_double_click_conf_threshold = 0.7  # уверенность, выше которой делаем два клика
rmb_double_click_interval = 0.1   # сек между первым и вторым кликом

# Вращение башней для поиска целей (когда цель не найдена)
tower_rotation_enabled = True  # Включить вращение при поиске
tower_rotation_pixels = 6  # Пикселей за один шаг (не быстро)
tower_rotation_interval = 0.18  # Секунд между шагами вращения
last_tower_rotation_time = 0.0
tower_rotation_direction = 1  # 1 = вправо, -1 = влево
tower_rotation_steps_before_flip = 25  # Шагов до смены направления (движение туда-обратно)
tower_rotation_step_count = 0  # Счётчик шагов в текущем направлении

# Поворот башни по циферблату: «N часов» → угол N*30° (12=0°, 3=90°, 6=180°, 9=270°)
# Та же база используется для наводки; чувствительность наводки можно ослабить отдельно
clock_pixels_per_90 = 1500  # пикселей мыши на поворот на 90°
AIM_SENSITIVITY_REFERENCE = 1500  # база для расчёта наводки (не трогать, если устраивает)
aim_sensitivity_factor = 0.7  # чувствительность наводки: меньше = плавнее (0.5 = вдвое слабее, 0.3 = в 3 раза)

# Микрокоррекция ствола по вертикали (поднять/опустить дуло/ствол)
barrel_adjust_pixels = 100  # пикселей мыши для небольшой коррекции ствола

# Индикация "цель захвачена" / "поиск" — звук и оверлей
target_status_overlay = None  # Окно оверлея (обновляется в потоке детекции)
last_search_beep_time = 0.0   # Когда последний раз пищали "поиск"
search_beep_interval = 2.0    # Секунд между звуками "поиск" (чтобы не спамить)
target_locked_beep_hz = 900   # Гц при захвате цели
target_locked_beep_ms = 200   # Длина бипа "цель захвачена"
search_beep_hz = 500          # Гц при поиске
search_beep_ms = 80           # Длина бипа "поиск"

q = queue.Queue()
last_voice_time = 0
last_command_time = 0  # Время последней выполненной команды
last_command = ""  # Последняя выполненная команда
command_cooldown = 0.2  # Минимальная задержка между командами (секунды)
last_shoot_time = 0.0  # Время последнего выстрела (для подавления ложных «отставить» и повторного «выстрел»)
POST_SHOOT_IGNORE_OTSTAVIT_SEC = 1.5  # после выстрела столько секунд игнорируем «отставить»
POST_SHOOT_DEBOUNCE_SEC = 1.0  # после выстрела столько секунд игнорируем второй «выстрел»

# Пилот: вкл — программа управляет танком, выкл — ждёт "включить пилота"
pilot_enabled = True

# Глобальный статус для оверлея: "В поиске цели" / "Зафиксирован на цели" / "Пилот отключён"
target_status_text = "В поиске цели"
STATUS_SEARCHING = "В поиске цели"
STATUS_LOCKED = "Зафиксирован на цели"
STATUS_PILOT_OFF = "Пилот отключён"

def play_target_locked_sound():
    """Звук: модель считает, что цель захвачена"""
    if WINSOUND_AVAILABLE:
        try:
            winsound.Beep(target_locked_beep_hz, target_locked_beep_ms)
        except Exception:
            pass

def play_searching_sound():
    """Короткий звук: цель не найдена, идёт поиск"""
    if WINSOUND_AVAILABLE:
        try:
            winsound.Beep(search_beep_hz, search_beep_ms)
        except Exception:
            pass

def run_menu_overlay():
    """Оверлей: только статус прицела/пилота, кнопка Вкл/Выкл и крестик закрытия (остановка программы)."""
    global target_status_text, pilot_enabled
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title("")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.9)
        root.resizable(False, False)
        root.configure(bg="#1a1a1a")
        root.geometry("+12+12")

        f = tk.Frame(root, bg="#1a1a1a", padx=6, pady=4)
        f.pack(fill=tk.X)

        lbl_status = tk.Label(f, text=target_status_text, font=("Consolas", 10, "bold"),
                              fg="yellow", bg="#1a1a1a", width=22, anchor=tk.W)
        lbl_status.pack(side=tk.LEFT, padx=(0, 6))

        def toggle_pilot():
            global pilot_enabled, target_status_text
            pilot_enabled = not pilot_enabled
            target_status_text = STATUS_PILOT_OFF if not pilot_enabled else STATUS_SEARCHING
            btn_toggle.config(text="Выкл" if pilot_enabled else "Вкл",
                             bg="#0a4a0a" if pilot_enabled else "#4a1a1a",
                             fg="lime" if pilot_enabled else "#c66")

        btn_toggle = tk.Button(f, text="Выкл" if pilot_enabled else "Вкл",
                               font=("Consolas", 9, "bold"), width=4,
                               command=toggle_pilot, cursor="hand2",
                               bg="#0a4a0a" if pilot_enabled else "#4a1a1a",
                               fg="lime" if pilot_enabled else "#c66",
                               relief=tk.FLAT, padx=4, pady=2)
        btn_toggle.pack(side=tk.LEFT, padx=(0, 4))

        def quit_app():
            root.quit()
            os._exit(0)

        btn_close = tk.Label(f, text="\u00d7", font=("Consolas", 14, "bold"),
                             fg="#888", bg="#1a1a1a", cursor="hand2",
                             activebackground="#3a2a2a", activeforeground="#f66")
        btn_close.pack(side=tk.RIGHT)
        btn_close.bind("<Button-1>", lambda e: quit_app())
        btn_close.bind("<Enter>", lambda e: btn_close.config(fg="#c66"))
        btn_close.bind("<Leave>", lambda e: btn_close.config(fg="#888"))

        def update_ui():
            global target_status_text, pilot_enabled
            s = target_status_text
            lbl_status.config(text=s)
            if s == STATUS_LOCKED:
                lbl_status.config(fg="lime", bg="#0a300a")
            elif s == STATUS_PILOT_OFF:
                lbl_status.config(fg="#888", bg="#2a1a1a")
            else:
                lbl_status.config(fg="yellow", bg="#1a1a1a")
            btn_toggle.config(text="Выкл" if pilot_enabled else "Вкл",
                             bg="#0a4a0a" if pilot_enabled else "#4a1a1a",
                             fg="lime" if pilot_enabled else "#c66")
            root.after(300, update_ui)

        root.after(300, update_ui)
        root.mainloop()
    except Exception as e:
        print(f"Оверлей не запущен: {e}")

def start_menu_overlay():
    """Запуск оверлея-меню в отдельном потоке."""
    try:
        t = threading.Thread(target=run_menu_overlay, daemon=True)
        t.start()
        print("Оверлей (статус + Вкл/Выкл + закрытие) запущен.")
    except Exception as e:
        print(f"Не удалось запустить оверлей-меню: {e}")

def run_status_overlay():
    """Окно в углу: текущее состояние — «В поиске цели» / «Зафиксирован на цели»"""
    global target_status_text
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title("")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.85)
        root.geometry("+12+12")
        root.configure(bg="black")
        lbl = tk.Label(root, text=target_status_text, font=("Consolas", 12, "bold"),
                       fg="yellow", bg="#1a1a1a", padx=10, pady=6)
        lbl.pack()

        def update_label():
            global target_status_text
            s = target_status_text
            lbl.config(text=s)
            if s == STATUS_LOCKED:
                lbl.config(fg="lime", bg="#0a300a")
            elif s == STATUS_PILOT_OFF:
                lbl.config(fg="#888", bg="#2a1a1a")
            else:
                lbl.config(fg="yellow", bg="#1a1a1a")
            root.after(250, update_label)

        root.after(250, update_label)
        root.mainloop()
    except Exception as e:
        print(f"Оверлей статуса не запущен: {e}")

def start_status_overlay():
    """Запуск оверлея в отдельном потоке"""
    try:
        t = threading.Thread(target=run_status_overlay, daemon=True)
        t.start()
        print("Оверлей статуса (ЦЕЛЬ/ПОИСК) запущен в углу экрана.")
    except Exception as e:
        print(f"Не удалось запустить оверлей: {e}")

def _vk_code(key):
    """Виртуальный код клавиши для win32 (w,a,s,d,1-6 и т.п.)."""
    if len(key) == 1 and key.isalpha():
        return ord(key.upper())
    return ord(key)

def _key_up(key):
    """Отпустить клавишу — через win32 в играх надёжнее, чем pyautogui."""
    try:
        if WIN32_AVAILABLE:
            win32api.keybd_event(_vk_code(key), 0, win32con.KEYEVENTF_KEYUP, 0)
        else:
            pyautogui.keyUp(key)
    except Exception:
        pyautogui.keyUp(key)

def _key_down(key):
    """Нажать клавишу (удерживать)."""
    try:
        if WIN32_AVAILABLE:
            win32api.keybd_event(_vk_code(key), 0, 0, 0)
        else:
            pyautogui.keyDown(key)
    except Exception:
        pyautogui.keyDown(key)

def _key_press(key):
    """Нажать и отпустить (один раз)."""
    _key_down(key)
    time.sleep(0.03)
    _key_up(key)

def turn_turret_to_clock(hour):
    """Поворот башни по циферблату: 12=0°, 3=90° вправо, 6=180°, 9=270° (влево). hour 1..12."""
    angle = (hour % 12) * 30
    if angle == 0:
        return
    P = clock_pixels_per_90
    if angle <= 180:
        dx = int((angle / 90) * P)
    else:
        dx = -int((360 - angle) / 90 * P)
    try:
        if WIN32_AVAILABLE:
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, dx, 0, 0, 0)
        elif MOUSE_AVAILABLE:
            mouse.move(dx, 0, absolute=False, duration=0.05)
        else:
            pyautogui.moveRel(dx, 0, duration=0.05)
    except Exception:
        pyautogui.moveRel(dx, 0, duration=0.05)
    print(f"--- ПОВОРОТ БАШНИ: {hour} ч ({angle}°) ---")

# N часов → угол N*30° (12=0°, 3=90°, 6=180°, 9=270°); используется для поворота башни
CLOCK_COMMANDS = {
    "один час": 1, "два часа": 2, "три часа": 3, "четыре часа": 4,
    "пять часов": 5, "шесть часов": 6, "семь часов": 7, "восемь часов": 8,
    "девять часов": 9, "десять часов": 10, "одиннадцать часов": 11,
    "двенадцать часов": 12,
}

def stop_all():
    """Функция отпускает все зажатые клавиши управления"""
    for key in CONTROLS:
        _key_up(key)
    print("--- ВСЕ КЛАВИШИ ОТПУЩЕНЫ ---")

def init_kronos():
    """Инициализация KRONOS-AI (загрузка модели и камеры)"""
    global kronos_model, kronos_camera
    
    if not YOLO_AVAILABLE or not DXCAM_AVAILABLE:
        return False
    
    try:
        if kronos_model is None:
            if os.path.exists(KRONOS_MODEL_PATH):
                print(f"Загрузка модели KRONOS: {KRONOS_MODEL_PATH}...")
                kronos_model = YOLO(KRONOS_MODEL_PATH)
                print("Модель KRONOS загружена успешно!")
            else:
                alt_paths = [
                    os.path.join(BASE_DIR, "kronos_models", "yolov12n.pt"),
                    os.path.join(BASE_DIR, "best.pt"),
                    os.path.join(BASE_DIR, "yolov12n.pt"),
                ]
                for path in alt_paths:
                    if os.path.exists(path):
                        print(f"Загрузка модели KRONOS: {path}...")
                        kronos_model = YOLO(path)
                        print("Модель KRONOS загружена успешно!")
                        break
                if kronos_model is None:
                    print("ВНИМАНИЕ: Модель KRONOS не найдена. Фоновая детекция не будет работать.")
                    return False
        
        if kronos_camera is None:
            try:
                kronos_camera = dxcam.create()
                print("Камера KRONOS инициализирована!")
            except Exception as e:
                print(f"Ошибка инициализации камеры: {e}")
                return False
        
        return True
    except Exception as e:
        print(f"Ошибка инициализации KRONOS: {e}")
        return False

def detect_enemy_tank(frame, model):
    """Определяет, является ли объект вражеским танком и возвращает координаты центра"""
    enemy_labels = [
        "enemy", "tank", "enemy_tank", "opponent", "target",
        "tank_enemy", "vehicle", "enemy_vehicle", "player"
    ]
    
    results = model(frame, verbose=False)
    
    best_enemy = None
    best_conf = 0.0
    best_center_x = 0
    best_center_y = 0
    
    for r in results:
        boxes = r.boxes
        for box in boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            conf = float(box.conf[0])
            cls = int(box.cls[0])
            label = model.names[cls].lower()
            is_enemy = any(enemy_label in label for enemy_label in enemy_labels)
            if is_enemy and conf > 0.4 and conf > best_conf:
                best_conf = conf
                best_enemy = label
                best_center_x = int(x1 + (x2 - x1) / 2)
                best_center_y = int(y1 + (y2 - y1) / 2)
    
    if best_enemy:
        return True, best_conf, best_enemy, best_center_x, best_center_y
    
    return False, 0.0, "", 0, 0

def background_detection_loop():
    """Фоновый цикл детекции вражеских танков"""
    global kronos_model, kronos_camera, auto_aim_enabled, last_right_click_time, right_click_cooldown, right_click_locked
    global last_tower_rotation_time, tower_rotation_direction, tower_rotation_step_count
    global target_status_text, last_search_beep_time, pilot_enabled, last_rmb_double_click_time

    time.sleep(2)
    
    try:
        if not init_kronos():
            print("KRONOS не готов для фоновой детекции. Проверьте наличие модели.")
            return
        
        if kronos_model is None or kronos_camera is None:
            print("KRONOS не инициализирован для фоновой детекции.")
            return
        
        print("--- ФОНОВАЯ ДЕТЕКЦИЯ АКТИВИРОВАНА ---")

        while auto_aim_enabled:
            try:
                if not pilot_enabled:
                    target_status_text = STATUS_PILOT_OFF
                    time.sleep(0.15)
                    continue
                if kronos_camera is None:
                    time.sleep(0.1)
                    continue
                    
                frame = kronos_camera.grab()
                if frame is None:
                    time.sleep(0.01)
                    continue

                if kronos_model is None:
                    time.sleep(0.1)
                    continue
                    
                is_enemy, conf, label, center_x, center_y = detect_enemy_tank(frame, kronos_model)
                target_status_text = STATUS_LOCKED if right_click_locked else STATUS_SEARCHING
                
                def move_mouse_to_target(cx, cy, frame_shape):
                    """Двигает прицел (мышь) к центру цели; чувствительность из clock_pixels_per_90."""
                    fh, fw = frame_shape[:2]
                    sw, sh = pyautogui.size()
                    sx = int(cx * sw / fw)
                    sy = int(cy * sh / fh)
                    if MOUSE_AVAILABLE:
                        mx, my = mouse.get_position()
                    else:
                        mx, my = pyautogui.position()
                    ox = sx - mx
                    oy = sy - my
                    scale = (clock_pixels_per_90 / AIM_SENSITIVITY_REFERENCE) * aim_sensitivity_factor
                    ox = int(ox * scale)
                    oy = int(oy * scale)
                    if abs(ox) <= 2 and abs(oy) <= 2:
                        return
                    try:
                        if WIN32_AVAILABLE:
                            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, ox, oy, 0, 0)
                        elif MOUSE_AVAILABLE:
                            mouse.move(ox, oy, absolute=False, duration=0.02)
                        else:
                            pyautogui.moveRel(ox, oy, duration=0.05)
                    except Exception:
                        pass
                
                if is_enemy and right_click_locked:
                    move_mouse_to_target(center_x, center_y, frame.shape)
                elif is_enemy and not right_click_locked:
                    current_time = time.time()
                    if current_time - last_right_click_time >= right_click_cooldown:
                        frame_height, frame_width = frame.shape[:2]
                        screen_width, screen_height = pyautogui.size()
                        scale_x = screen_width / frame_width
                        scale_y = screen_height / frame_height
                        scx = int(center_x * scale_x)
                        scy = int(center_y * scale_y)
                        if MOUSE_AVAILABLE:
                            current_mouse_x, current_mouse_y = mouse.get_position()
                        else:
                            current_mouse_x, current_mouse_y = pyautogui.position()
                        offset_x = scx - current_mouse_x
                        offset_y = scy - current_mouse_y
                        aim_scale = (clock_pixels_per_90 / AIM_SENSITIVITY_REFERENCE) * aim_sensitivity_factor
                        offset_x = int(offset_x * aim_scale)
                        offset_y = int(offset_y * aim_scale)
                        print(f"Танк обнаружен: {label} (уверенность: {conf:.2f}) - смещение ({offset_x:.0f}, {offset_y:.0f})")
                        if abs(offset_x) > 2 or abs(offset_y) > 2:
                            try:
                                if WIN32_AVAILABLE:
                                    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, offset_x, offset_y, 0, 0)
                                    time.sleep(0.05)
                                elif MOUSE_AVAILABLE:
                                    mouse.move(offset_x, offset_y, absolute=False, duration=0.05)
                                    time.sleep(0.05)
                                else:
                                    pyautogui.moveRel(offset_x, offset_y, duration=0.1)
                                    time.sleep(0.05)
                            except Exception as e:
                                print(f"Ошибка мыши: {e}")
                        time.sleep(0.1)
                        if MOUSE_AVAILABLE:
                            mouse.click(button='right')
                        else:
                            pyautogui.click(button='right')
                        last_right_click_time = current_time
                        right_click_locked = True
                        play_target_locked_sound()
                        print(f"Обнаружен вражеский танк: {label} (уверенность: {conf:.2f}) - Наведение и ПКМ [ЗАФИКСИРОВАН НА ЦЕЛИ]")
                if not is_enemy:
                    if tower_rotation_enabled and tower_rotation_interval > 0:
                        current_time = time.time()
                        if current_time - last_tower_rotation_time >= tower_rotation_interval:
                            last_tower_rotation_time = current_time
                            dx = tower_rotation_pixels * tower_rotation_direction
                            try:
                                if WIN32_AVAILABLE:
                                    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, int(dx), 0, 0, 0)
                                elif MOUSE_AVAILABLE:
                                    mouse.move(int(dx), 0, absolute=False, duration=0.02)
                                else:
                                    pyautogui.moveRel(int(dx), 0, duration=0.02)
                            except Exception:
                                pass
                            tower_rotation_step_count += 1
                            if tower_rotation_step_count >= tower_rotation_steps_before_flip:
                                tower_rotation_step_count = 0
                                tower_rotation_direction *= -1
                    current_time = time.time()
                    if current_time - last_search_beep_time >= search_beep_interval:
                        last_search_beep_time = current_time
                        play_searching_sound()
                time.sleep(0.01)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                time.sleep(0.1)
    except Exception as e:
        print(f"Критическая ошибка в фоновой детекции: {e}")
        print("Фоновая детекция остановлена, но голосовые команды продолжают работать.")

def start_background_detection():
    """Запускает фоновую детекцию в отдельном потоке"""
    global auto_aim_thread, auto_aim_enabled
    
    if auto_aim_thread is None or not auto_aim_thread.is_alive():
        try:
            auto_aim_enabled = True
            auto_aim_thread = threading.Thread(target=background_detection_loop, daemon=True)
            auto_aim_thread.start()
            print("Фоновая детекция запущена")
        except Exception as e:
            print(f"Ошибка запуска фоновой детекции: {e}")
            print("Продолжаем работу без фоновой детекции...")

def shoot():
    """Выстрел - нажатие левой кнопкой мыши"""
    pyautogui.click(button='left')
    print("--- ВЫСТРЕЛ ---")

def process_command(cmd):
    """Обрабатывает голосовую команду (оптимизированная версия)"""
    global right_click_locked, last_command_time, last_command, target_status_text, pilot_enabled, last_shoot_time
    
    print(f"[DEBUG] process_command вызвана с командой: '{cmd}'")
    if not pilot_enabled:
        if cmd == 'включить пилота':
            pilot_enabled = True
            target_status_text = STATUS_SEARCHING
            print("--- ПИЛОТ ВКЛЮЧЁН. УПРАВЛЕНИЕ ВОЗОБНОВЛЕНО ---")
        return
    now = time.time()
    if cmd == 'отставить' and last_shoot_time and (now - last_shoot_time) < POST_SHOOT_IGNORE_OTSTAVIT_SEC:
        return
    if cmd in ('выстрел', 'огонь') and last_shoot_time and (now - last_shoot_time) < POST_SHOOT_DEBOUNCE_SEC:
        return
    current_time = time.time()
    if cmd == last_command and current_time - last_command_time < command_cooldown:
        print(f"[DEBUG] Команда '{cmd}' пропущена из-за кулдауна")
        return
    
    last_command_time = current_time
    last_command = cmd
    
    print(f"[DEBUG] Выполняю команду: '{cmd}'")
    try:
        if cmd == 'отключить пилота':
            pilot_enabled = False
            target_status_text = STATUS_PILOT_OFF
            stop_all()
            print("--- ПИЛОТ ОТКЛЮЧЁН. Скажите «включить пилота» для возобновления ---")
        elif cmd == 'включить пилота':
            pilot_enabled = True
            target_status_text = STATUS_SEARCHING
            print("--- ПИЛОТ ВКЛЮЧЁН ---")
        elif cmd == 'стоп':
            print("[DEBUG] Выполняю stop_all()")
            stop_all()
        elif cmd == 'стоп влево':
            _key_up("a")
        elif cmd == 'стоп вправо':
            _key_up("d")
        elif cmd == 'стоп перед':
            _key_up("w")
        elif cmd == 'стоп назад':
            _key_up("s")
        elif cmd == 'влево' or cmd == 'налево':
            _key_down("a")
            time.sleep(turn_key_hold_duration)
            _key_up("a")
        elif cmd == 'вправо' or cmd == 'направо':
            _key_down("d")
            time.sleep(turn_key_hold_duration)
            _key_up("d")
        elif cmd == 'перед' or cmd == 'вперед' or cmd == 'прямо':
            _key_up("s")
            _key_down("w")
        elif cmd == 'назад':
            _key_up("w")
            _key_down("s")
        elif cmd == 'снаряд один':
            _key_press("1")
        elif cmd == 'снаряд два':
            _key_press("2")
        elif cmd == 'снаряд три':
            _key_press("3")
        elif cmd == 'снаряжение один':
            _key_press("4")
        elif cmd == 'снаряжение два':
            _key_press("5")
        elif cmd == 'снаряжение три':
            _key_press("6")
        elif cmd == 'активировать снайперский':
            print("[DEBUG] scroll(1000)")
            pyautogui.scroll(1000)
        elif cmd == 'деактивировать снайперский':
            print("[DEBUG] scroll(-1000)")
            pyautogui.scroll(-1000)
        elif cmd == 'выстрел' or cmd == 'огонь':
            shoot()
            last_shoot_time = time.time()
        elif cmd == 'отставить':
            print("[DEBUG] click('right') и снятие блокировки")
            pyautogui.click(button='right')
            right_click_locked = False
            target_status_text = STATUS_SEARCHING
            play_searching_sound()
            print("--- БЛОКИРОВКА ПКМ СНЯТА. В ПОИСКЕ ЦЕЛИ ---")
        elif cmd in ('поднять дуло', 'поднять ствол'):
            try:
                if WIN32_AVAILABLE:
                    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, -barrel_adjust_pixels, 0, 0)
                elif MOUSE_AVAILABLE:
                    mouse.move(0, -barrel_adjust_pixels, absolute=False, duration=0.02)
                else:
                    pyautogui.moveRel(0, -barrel_adjust_pixels, duration=0.02)
            except Exception:
                pyautogui.moveRel(0, -barrel_adjust_pixels, duration=0.02)
            print("--- СТВОЛ ПОДНЯТ ---")
        elif cmd in ('опустить дуло', 'опустить ствол'):
            try:
                if WIN32_AVAILABLE:
                    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, barrel_adjust_pixels, 0, 0)
                elif MOUSE_AVAILABLE:
                    mouse.move(0, barrel_adjust_pixels, absolute=False, duration=0.02)
                else:
                    pyautogui.moveRel(0, barrel_adjust_pixels, duration=0.02)
            except Exception:
                pyautogui.moveRel(0, barrel_adjust_pixels, duration=0.02)
            print("--- СТВОЛ ОПУЩЕН ---")
        elif cmd in CLOCK_COMMANDS:
            turn_turret_to_clock(CLOCK_COMMANDS[cmd])
        else:
            print(f"[DEBUG] Неизвестная команда: '{cmd}'")
        print(f"[DEBUG] Команда '{cmd}' выполнена успешно")
    except Exception as e:
        print(f"[ERROR] Ошибка выполнения команды '{cmd}': {e}")
        import traceback
        traceback.print_exc()

def callback(indata, frames, time_info, status):
    q.put(bytes(indata))


model = Model(MODEL_PATH)
rec = KaldiRecognizer(model, SAMPLE_RATE, WORDS_SPEC)

# Список всех голосовых команд для ранней реакции по PartialResult (длинные первыми)
VOICE_COMMANDS_LIST = sorted(
    ["отключить пилота", "включить пилота", "деактивировать снайперский", "активировать снайперский",
     "стоп влево", "стоп вправо", "стоп перед", "стоп назад",
     "снаряжение один", "снаряжение два", "снаряжение три",
     "снаряд один", "снаряд два", "снаряд три", "отставить", "стоп", "влево", "вправо", "перед", "назад", "выстрел",
     "огонь", "налево", "направо", "прямо", "вперед",
     "поднять дуло", "поднять ствол", "опустить дуло", "опустить ствол"]
    + list(CLOCK_COMMANDS.keys()),
    key=len, reverse=True
)
# Команды, которые НЕ запускаем по partial — только по финалу (меньше ложных срабатываний)
# влево/вправо/перед/назад — путаются; выстрел/отставить — от одного «выстрел» partial даёт выстрел, потом ложные «отставить», потом финал «выстрел» снова
PARTIAL_EXCLUDED_COMMANDS = {"влево", "вправо", "перед", "назад", "выстрел", "отставить", "огонь", "налево", "направо", "прямо", "вперед", "поднять дуло", "поднять ствол", "опустить дуло", "опустить ствол"}
# Дебаунс: не выполнять ту же команду из partial дважды и не дублировать при приходе final
last_partial_cmd = None
last_partial_time = 0.0
PARTIAL_DEBOUNCE_SEC = 0.6  # сек: если команду уже выполняли из partial, final с той же командой пропускаем

print("Система готова. Начните говорить...")
start_menu_overlay()
try:
    print("Запуск фоновой детекции вражеских танков...")
    start_background_detection()
except Exception as e:
    print(f"Не удалось запустить фоновую детекцию: {e}")
    print("Продолжаем работу без фоновой детекции...")

# blocksize (сэмплов): меньше = чаще обновления Vosk, быстрее реакция (800 ≈ 50 ms при 16 kHz)
VOICE_BLOCKSIZE = 800
with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=VOICE_BLOCKSIZE,
                       dtype='int16', channels=1, callback=callback):
    while True:
        try:
            data = q.get()
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                command = result.get('text', '')
                if command:
                    cmd = command.lower().strip()
                    now = time.time()
                    if last_partial_cmd == cmd and (now - last_partial_time) < PARTIAL_DEBOUNCE_SEC:
                        pass
                    else:
                        print(f"[MAIN] Финал: '{cmd}'")
                        try:
                            process_command(cmd)
                        except Exception as cmd_error:
                            print(f"[ERROR] Ошибка для '{cmd}': {cmd_error}")
                            import traceback
                            traceback.print_exc()
            else:
                part_raw = rec.PartialResult()
                try:
                    part = json.loads(part_raw).get('partial', '').lower().strip()
                except Exception:
                    part = ''
                if part:
                    for c in VOICE_COMMANDS_LIST:
                        if c in PARTIAL_EXCLUDED_COMMANDS:
                            continue
                        if part == c or part.endswith(' ' + c):
                            now = time.time()
                            if last_partial_cmd == c and (now - last_partial_time) < PARTIAL_DEBOUNCE_SEC:
                                break
                            last_partial_cmd = c
                            last_partial_time = now
                            print(f"[MAIN] Частичный (ранний запуск): '{c}'")
                            try:
                                process_command(c)
                            except Exception as e:
                                print(f"[ERROR] Частичный '{c}': {e}")
                            break
        except KeyboardInterrupt:
            print("\nОстановка системы...")
            stop_all()
            break
        except Exception as e:
            print(f"Ошибка в основном цикле: {e}")
            time.sleep(0.1)



