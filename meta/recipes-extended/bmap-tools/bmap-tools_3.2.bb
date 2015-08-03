DESCRIPTION = "Tools to generate block map (AKA bmap) and flash images using bmap."
HOMEPAGE = "http://source.tizen.org/documentation/reference/bmaptool/bmap-tools-project"
SECTION = "console/tools"
LICENSE = "GPLv2.0"
LIC_FILES_CHKSUM = "file://COPYING;md5=b234ee4d69f5fce4486a80fdaf4a4263"

SRC_URI = "ftp://ftp.infradead.org/pub/${BPN}/${BPN}-${PV}.tgz"
SRC_URI[md5sum] = "92cdad1cb4dfa0cca7176c8e22752616"
SRC_URI[sha256sum] = "cc6c7f7dc0a37e2a32deb127308e24e6c4b80bfb54f3803c308efab02bf2d434"

RDEPENDS_${PN} = "bzip2 gzip lzop pigz python tar xz"

inherit setuptools

BBCLASSEXTEND = "native"
