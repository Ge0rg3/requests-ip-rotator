import pathlib
from setuptools import setup, find_packages

location = pathlib.Path(__file__).parent
README = (location / "README.md").read_text(encoding="utf-8")

requirements_path = location / "requirements.txt"
if requirements_path.exists():
    with open(requirements_path, encoding="utf-8") as f:
        requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

setup(
    name="requests-ip-rotator",
    version="1.0.17",
    description="A Python library to rotate IP addresses using AWS API Gateway",
    long_description=README,
    long_description_content_type="text/markdown",
    url="https://github.com/Ge0rg3/requests-ip-rotator",
    project_urls={
        "Bug Reports": "https://github.com/Ge0rg3/requests-ip-rotator/issues",
        "Source": "https://github.com/Ge0rg3/requests-ip-rotator",
        "Documentation": "https://github.com/Ge0rg3/requests-ip-rotator#readme",
    },
    author="George Omnet",
    author_email="pypi@georgeom.net",
    maintainer="George Omnet",
    maintainer_email="pypi@georgeom.net",
    license="GPLv3+",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: Internet :: Proxy Servers",
        "Framework :: AsyncIO",
    ],
    packages=find_packages(include=["requests_ip_rotator", "requests_ip_rotator.*"]),
    include_package_data=True,
    install_requires=requirements,
    python_requires=">=3.10",
    keywords=[
        "aws",
        "api-gateway",
        "ip-rotation", 
        "proxy",
        "requests",
        "web-scraping",
        "http-client",
    ],
    platforms=["any"],
    zip_safe=False,
)