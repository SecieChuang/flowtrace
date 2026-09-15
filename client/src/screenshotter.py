"""screenshotter.py - 活跃期间定时截屏，保存到本地。

用法:
    python screenshotter.py
    python screenshotter.py --config path/to/config.json
    python screenshotter.py --cleanup-only --config path/to/config.json
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

_DIR = Path(__file__).resolve().parent
CLIENT_DIR = _DIR.parent
DEFAULT_CONFIG_PATH = CLIENT_DIR / "config" / "config.json"
SCREENSHOTS_DIR = CLIENT_DIR / "screenshots"


def load_config(path: Path) -> Dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as e:
            print(f"WARNING: Failed to parse config {path}: {e}", file=sys.stderr)
    return {}


def cleanup_old_screenshots(retention_days: int) -> None:
    if retention_days <= 0:
        return
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    # 每天只清理一次，用哨兵文件记录上次清理日期
    sentinel = SCREENSHOTS_DIR / ".last_cleanup"
    today = datetime.now().date()
    if sentinel.exists():
        try:
            if datetime.fromtimestamp(sentinel.stat().st_mtime).date() >= today:
                return
        except OSError:
            pass
    cutoff = datetime.now() - timedelta(days=retention_days)
    for day_dir in SCREENSHOTS_DIR.iterdir():
        if not day_dir.is_dir() or day_dir.name.startswith("."):
            continue
        try:
            dir_date = datetime.strptime(day_dir.name, "%Y-%m-%d")
        except ValueError:
            continue
        if dir_date.date() < cutoff.date():
            for img in day_dir.glob("*.jpg"):
                try:
                    img.unlink()
                except OSError:
                    pass
            try:
                day_dir.rmdir()
            except OSError:
                pass
    try:
        sentinel.touch()
    except OSError:
        pass


def take_screenshot(quality: int, max_dimension: int, output_path: Path, capture_webcam: bool = False) -> None:
    from PIL import Image, ImageGrab

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img = ImageGrab.grab(all_screens=True)

    if capture_webcam:
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                cap.release()
                print("SCREENSHOT_WARNING|msg=webcam unavailable", file=sys.stderr)
            if cap.isOpened():
                # 请求摄像头最高可用分辨率，减少后续放大倍数
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 4096)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 4096)
                # 让摄像头自动曝光/白平衡稳定，丢弃前 20 帧
                for _ in range(20):
                    cap.read()
                ret, frame = cap.read()
                cap.release()

                if ret:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    cam = Image.fromarray(frame_rgb)
                    is_landscape = cam.width >= cam.height

                    if is_landscape:
                        scale = img.width / cam.width
                        cam = cam.resize((img.width, int(cam.height * scale)), Image.LANCZOS)
                    else:
                        scale = img.height / cam.height
                        cam = cam.resize((int(cam.width * scale), img.height), Image.LANCZOS)

                    # 大幅放大后锐化，恢复细节
                    if scale > 1.5:
                        from PIL import ImageFilter
                        cam = cam.filter(ImageFilter.SHARPEN)
                        if scale > 3:
                            cam = cam.filter(ImageFilter.SHARPEN)

                    if is_landscape:
                        merged = Image.new('RGB', (img.width, img.height + cam.height))
                        merged.paste(img, (0, 0))
                        merged.paste(cam, (0, img.height))
                    else:
                        merged = Image.new('RGB', (img.width + cam.width, img.height))
                        merged.paste(img, (0, 0))
                        merged.paste(cam, (img.width, 0))

                    img = merged
        except ImportError:
            print("SCREENSHOT_WARNING|msg=cv2 not installed, webcam skipped", file=sys.stderr)
        except Exception as e:
            print(f"SCREENSHOT_WARNING|msg=webcam failed: {e}", file=sys.stderr)

    w, h = img.size
    if max(w, h) > max_dimension:
        ratio = max_dimension / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    img.save(str(output_path), "JPEG", quality=quality, optimize=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Take a screenshot during active period")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--cleanup-only", action="store_true", help="Only run retention cleanup, no screenshot")
    webcam_group = parser.add_mutually_exclusive_group()
    webcam_group.add_argument("--webcam", action="store_true", default=None, help="Force enable webcam capture")
    webcam_group.add_argument("--no-webcam", action="store_true", default=None, help="Force disable webcam capture")
    args = parser.parse_args(argv or sys.argv[1:])

    cfg = load_config(Path(args.config))
    retention_days = int(cfg.get("screenshot_retention_days", 7))
    cleanup_old_screenshots(retention_days)

    if args.cleanup_only:
        return 0

    if not cfg.get("screenshot_enabled", True):
        return 0

    quality = int(cfg.get("screenshot_quality", 60))
    max_dimension = int(cfg.get("screenshot_max_dimension", 1280))

    # 命令行 --webcam/--no-webcam 优先于配置文件
    if args.no_webcam:
        capture_webcam = False
    elif args.webcam:
        capture_webcam = True
    else:
        capture_webcam = bool(cfg.get("screenshot_capture_webcam", False))

    now = datetime.now()
    output_path = SCREENSHOTS_DIR / now.strftime("%Y-%m-%d") / f"{now.strftime('%H-%M-%S')}.jpg"

    try:
        take_screenshot(quality, max_dimension, output_path, capture_webcam)
        print(f"SCREENSHOT_OK|path={output_path}")
        return 0
    except Exception as e:
        print(f"SCREENSHOT_ERROR|error={e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
