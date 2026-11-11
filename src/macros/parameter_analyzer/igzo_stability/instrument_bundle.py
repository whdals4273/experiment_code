from __future__ import annotations

__version__ = "0.2.0"

from dataclasses import dataclass

import pyvisa

from src.macros.oscilloscope.oscilloscope_macro import OscopeMacro
from src.macros.parameter_analyzer.basic_pa_macro import ParameterAnalyzerMacro
from src.macros.pulse_generator.pulse_generator_macro import PulseGenMacro


@dataclass
class IGZOStabilityInstrumentBundle:
    rm: pyvisa.ResourceManager
    analyzer_macro: ParameterAnalyzerMacro
    pulsegen_macro: PulseGenMacro
    oscope_macro: OscopeMacro

    def disconnect(self) -> None:
        try:
            self.oscope_macro.disconnect()
        except Exception:
            pass
        try:
            self.pulsegen_macro.disconnect()
        except Exception:
            pass
        try:
            self.analyzer_macro.disconnect()
        except Exception:
            pass
        try:
            self.rm.close()
        except Exception:
            pass

    def shutdown(self) -> None:
        self.disconnect()


def build_igzo_stability_bundle(
    rm: pyvisa.ResourceManager,
    *,
    analyzer_resource: str,
    pulsegen_resource: str = "PulseGen",
    oscope_resource: str = "Oscope",
) -> IGZOStabilityInstrumentBundle:
    analyzer_macro = ParameterAnalyzerMacro(resource_manager=rm, resource_name=analyzer_resource)
    pulsegen_macro = PulseGenMacro(resource_manager=rm, resource_name=pulsegen_resource)
    oscope_macro = OscopeMacro(resource_manager=rm, resource_name=oscope_resource)
    return IGZOStabilityInstrumentBundle(
        rm=rm,
        analyzer_macro=analyzer_macro,
        pulsegen_macro=pulsegen_macro,
        oscope_macro=oscope_macro,
    )
