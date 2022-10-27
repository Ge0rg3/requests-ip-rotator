import pathlib
from setuptools import setup

location = pathlib.Path(__file__).parent

# The text of the README file
README = (location / "README.md").read_text()

# This call to setup() does all the work
setup(
    name="requests-ip-rotator",
    version="1.0.14",
    description="Rotate through IPs in Python using AWS API Gateway.",
    long_description=README,
    long_description_content_type="text/markdown",
    url="https://github.com/Ge0rg3/requests-ip-rotator",
    author="George Omnet",
    author_email="pypi@georgeom.net",
    license="GPLv3+",
    classifiers=[
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.9",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering",
        "Topic :: System :: Archiving",
        "Topic :: Internet :: WWW/HTTP :: Indexing/Search",
        "Topic :: Internet :: WWW/HTTP",
    ],
    packages=["requests_ip_rotator"],
    include_package_data=True,
    install_requires=["requests", "boto3"]
)
