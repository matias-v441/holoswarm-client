import dearpygui.dearpygui as dpg
from gui.map.window import MapGridWindow
from data.mission import Mission
from data.session import Session
from gui.map.model import Map
from gui.explorer.window import ExplorerWindow 

LAYOUT_FILE = "user_layout.ini"


def run():
    dpg.create_context()

    dpg.configure_app(
        docking=True,
        docking_space=True,
        docking_shift_only=False,
        init_file=LAYOUT_FILE
    )

    mission = Mission()
    session = Session()

    map = Map(mission=mission, session=session)
    canvas = MapGridWindow(map)
    canvas.add()

    explorer = ExplorerWindow(mission,session)
    explorer.add()

    dpg.create_viewport(title="Docking example", width=1000, height=700)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    try:
        dpg.start_dearpygui()
    finally:
        dpg.save_init_file(LAYOUT_FILE)
        dpg.destroy_context()

if __name__ == "__main__":
    run()