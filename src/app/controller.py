from driver import api
from .driver_contract import DRIVER_FUNCS
from .settings import Settings
import matplotlib.pyplot as plt
import datetime

driver = DRIVER_FUNCS

class Controller:
    def __init__(self):
        self.streaming = False
        self.default_integration_time_ms = Settings.DEFAULT_INTEGRATION_TIME_MS
        self.default_continuous_acquisition_time_ms = Settings.DEFAULT_CONTINUOUS_ACQUISITION_TIME_MS
        self.default_single_spectrum_filename = datetime.datetime.now().strftime("spectrum_%Y%m%d_%H%M%S.csv")
        #self.dark_reference = DarkReference() #brauchen wir überhaupt eine?
    
    def single_spectrum(self, integration_time_ms: int = None, display: bool = False, save: bool = True, filename: str = None):
        if integration_time_ms is None:
            integration_time_ms = self.default_integration_time_ms
        if filename is None:
            filename = self.default_single_spectrum_filename
        spectrum = driver['get_single_spectrum'](integration_time_ms)
        if display:
            plt.figure()
            plt.plot(spectrum)
            plt.title(f'Single Spectrum (Integration Time: {integration_time_ms} ms)')
            plt.xlabel('Wavelength')
            plt.ylabel('Intensity')
            plt.show()
        if save:
            with open(filename, 'w') as f:
                for intensity in spectrum:
                    f.write(f"{intensity}\n")
                    
                    
            
    