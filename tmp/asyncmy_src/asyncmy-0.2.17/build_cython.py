import sys

from Cython.Build import cythonize
from setuptools import Extension
from setuptools.command.build_ext import build_ext

COMPILER_DIRECTIVES = {
    "language_level": 3,
    "cdivision": True,
    "initializedcheck": False,
}

if sys.platform == "win32":
    EXTRA_COMPILE_ARGS = ["/O2"]
else:
    EXTRA_COMPILE_ARGS = ["-O3"]


def build(setup_kwargs):
    setup_kwargs.update(
        {
            "ext_modules": cythonize(
                [
                    Extension(
                        "asyncmy.*",
                        ["asyncmy/*.pyx"],
                        extra_compile_args=EXTRA_COMPILE_ARGS,
                    ),
                ],
                compiler_directives=COMPILER_DIRECTIVES,
            ),
            "cmdclass": {"build_ext": build_ext},
        }
    )
