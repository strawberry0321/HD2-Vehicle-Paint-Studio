from desktop_server import main
from app_version import TITLE

if __name__ == "__main__":
    try: main()
    except Exception as error:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0,"启动失败："+str(error),TITLE,16)
        raise SystemExit(1)
