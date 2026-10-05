"""Run from Visual: python setup_native.py build_ext --inplace."""
from pathlib import Path
import os

from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

os.chdir(Path(__file__).resolve().parent)
setup(
    name="plant-disease-native",
    version="0.1.0",
    package_dir={"Visual": "."},
    ext_modules=[Pybind11Extension(
        "Visual.recognition._native", ["cpp/postprocess.cpp"], cxx_std=17,
        extra_compile_args=["/utf-8"] if os.name == "nt" else [],
    )],
    cmdclass={"build_ext": build_ext},
)
