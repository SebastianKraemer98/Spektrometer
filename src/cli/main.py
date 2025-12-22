import typing
from typing import Optional
from app import controller as controller

def single(
    plot: bool = False,
    save: bool = True,
    filename: Optional[str] = None,
    integration_time_ms: Optional[int] = None
):
    
    try:
        #check_connection()
        controller.single_spectrum(
            integration_time_ms=integration_time_ms,
            display=plot,
            save=save,
            filename=filename
        )
    except RuntimeError as e:
        print(f"Error: {e}")
        return