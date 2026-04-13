*Custom Compiler Version V-2023.12-6
*Mon Apr 13 00:54:35 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : OTA
* Cell             : 5T_OTA
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt 5T_OTA Bias GND OUT VDD VINN VINP
*.PININFO Bias:B GND:B OUT:B VDD:B VINN:B VINP:B
MM3 Bias net16 GND GND n08 l=0.014u nf=4 nfin=2
MM2 OUT VINN net7 GND n08 l=0.014u nf=4 nfin=2
MM1 net24 VINP net7 GND n08 l=0.014u nf=4 nfin=2
MM0 net7 net16 GND GND n08 l=0.014u nf=2 nfin=2
MM5 OUT net24 VDD VDD p08 l=0.014u nf=4 m=1 nfin=2
MM4 net24 net24 VDD VDD p08 l=0.014u nf=8 m=1 nfin=2
.ends 5T_OTA


