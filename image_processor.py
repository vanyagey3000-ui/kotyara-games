"""
Обработчик изображений — берёт картинки из static/images/
и создаёт круглые аватары для шайб + обрезанный фон
"""

import os
from PIL import Image, ImageDraw, ImageFilter

IMAGES_DIR = os.path.join(os.path.dirname(__file__), 'static', 'images')
PROCESSED_DIR = os.path.join(os.path.dirname(__file__), 'static', 'images', 'processed')


def ensure_dirs():
    """Создаём папку для обработанных картинок"""
    os.makedirs(PROCESSED_DIR, exist_ok=True)


def make_circle_avatar(input_path, output_path, size=120):
    """
    Берёт любую картинку и делает круглый аватар для клюшки/шайбы.
    Обрезает по центру, делает круг с прозрачным фоном.
    """
    try:
        img = Image.open(input_path).convert('RGBA')

        # Обрезаем по центру в квадрат
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))

        # Ресайз
        img = img.resize((size, size), Image.LANCZOS)

        # Создаём круглую маску
        mask = Image.new('L', (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size - 1, size - 1), fill=255)

        # Применяем лёгкое размытие к краям маски для сглаживания
        mask = mask.filter(ImageFilter.GaussianBlur(1))

        # Создаём результат с прозрачным фоном
        result = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        result.paste(img, (0, 0), mask)

        # Добавляем обводку
        draw_result = ImageDraw.Draw(result)
        draw_result.ellipse((1, 1, size - 2, size - 2), outline=(255, 255, 255, 180), width=3)

        result.save(output_path, 'PNG')
        print(f"  ✅ Аватар создан: {output_path}")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка обработки {input_path}: {e}")
        return False


def make_circle_puck(input_path, output_path, size=60):
    """
    Маленький круглый аватар для шайбы
    """
    return make_circle_avatar(input_path, output_path, size)


def process_background(input_path, output_path, width=800, height=500):
    """
    Обрезает фон под размер поля.
    Затемняет немного для контраста.
    """
    try:
        img = Image.open(input_path).convert('RGBA')

        # Ресайз с сохранением пропорций и кроп
        img_ratio = img.width / img.height
        target_ratio = width / height

        if img_ratio > target_ratio:
            # Картинка шире — обрезаем по бокам
            new_h = img.height
            new_w = int(new_h * target_ratio)
            left = (img.width - new_w) // 2
            img = img.crop((left, 0, left + new_w, new_h))
        else:
            # Картинка выше — обрезаем сверху/снизу
            new_w = img.width
            new_h = int(new_w / target_ratio)
            top = (img.height - new_h) // 2
            img = img.crop((0, top, new_w, top + new_h))

        img = img.resize((width, height), Image.LANCZOS)

        # Затемняем для контраста (оверлей)
        overlay = Image.new('RGBA', (width, height), (10, 20, 35, 140))
        img = Image.alpha_composite(img, overlay)

        img.save(output_path, 'PNG')
        print(f"  ✅ Фон обработан: {output_path}")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка обработки фона: {e}")
        return False


def process_all_images():
    """
    Обрабатывает все картинки из static/images/
    Создаёт круглые аватары и подготовленный фон
    """
    ensure_dirs()

    print("\n🎨 Обработка изображений...")
    print("=" * 40)

    # Персонажи — круглые аватары для клюшек
    characters = ['kompot', 'karamelka', 'korzhik', 'papa', 'mama',
                  'babushka', 'dedushka', 'nuke_kompot', 'cyber_karamelka',
                  'lyapochka', 'bantik']

    for char in characters:
        for ext in ['.png', '.jpg', '.jpeg', '.webp']:
            src = os.path.join(IMAGES_DIR, char + ext)
            if os.path.exists(src):
                # Большой аватар для клюшки
                dst_paddle = os.path.join(PROCESSED_DIR, f'{char}_paddle.png')
                make_circle_avatar(src, dst_paddle, size=120)

                # Маленький аватар для шайбы
                dst_puck = os.path.join(PROCESSED_DIR, f'{char}_puck.png')
                make_circle_puck(src, dst_puck, size=60)

                # Аватар для UI (профиль, магазин)
                dst_ui = os.path.join(PROCESSED_DIR, f'{char}_ui.png')
                make_circle_avatar(src, dst_ui, size=200)

                break
        else:
            print(f"  ⚠️ Картинка не найдена: {char}.png — будет эмодзи")

    # Фон стола
    for ext in ['.png', '.jpg', '.jpeg', '.webp']:
        bg_src = os.path.join(IMAGES_DIR, 'background' + ext)
        if os.path.exists(bg_src):
            bg_dst = os.path.join(PROCESSED_DIR, 'table_bg.png')
            process_background(bg_src, bg_dst)
            break
    else:
        print("  ⚠️ Фон не найден: background.png — будет стандартный цвет")

    print("=" * 40)
    print("🎨 Обработка завершена!\n")


def has_processed_image(name):
    """Проверяет, есть ли обработанная картинка"""
    path = os.path.join(PROCESSED_DIR, f'{name}.png')
    return os.path.exists(path)


def get_processed_url(name):
    """Возвращает URL обработанной картинки или None"""
    if has_processed_image(name):
        return f'/static/images/processed/{name}.png'
    return None


if __name__ == '__main__':
    process_all_images()
