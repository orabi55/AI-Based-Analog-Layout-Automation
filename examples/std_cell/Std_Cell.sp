*Custom Compiler Version V-2023.12-6
*Wed Mar 11 23:59:00 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : Test
* Cell             : Inverter
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt Inverter VDD VIN VOUT VSS
*.PININFO VDD:B VIN:I VOUT:O VSS:B
MM7 VOUT VIN VDD VDD p08 l=0.014u nf=1 m=1 nfin=5
MM8 VOUT VIN VSS VSS n08 l=0.014u nf=1 m=1 nfin=5
.ends Inverter

********************************************************************************
* Library          : Test
* Cell             : Xor
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt Xor A B OUT VDD VSS
*.PININFO A:I B:I OUT:O VDD:B VSS:B
MM28 Bb B VSS VSS n08 l=0.014u nf=2 nfin=4
MM5 PDN Bb VSS VSS n08 l=0.014u nf=2 nfin=4
MM4 PDN A VSS VSS n08 l=0.014u nf=2 nfin=4
MM3 OUT B PDN VSS n08 l=0.014u nf=2 nfin=4
MM2 OUT Ab PDN VSS n08 l=0.014u nf=2 nfin=4
MM25 Ab A VSS VSS n08 l=0.014u nf=2 nfin=4
MM29 Bb B VDD VDD p08 l=0.014u nf=2 m=1 nfin=4
MM9 PUN Ab VDD VDD p08 l=0.014u nf=2 m=1 nfin=4
MM8 PUN Bb VDD VDD p08 l=0.014u nf=2 m=1 nfin=4
MM7 OUT B PUN VDD p08 l=0.014u nf=2 m=1 nfin=4
MM6 OUT A PUN VDD p08 l=0.014u nf=2 m=1 nfin=4
MM24 Ab A VDD VDD p08 l=0.014u nf=2 m=1 nfin=4
.ends Xor

********************************************************************************
* Library          : Test
* Cell             : Std_Cell
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt Std_Cell VDD VIN1 VIN2 VOUT1 VOUT2 VSS
*.PININFO VDD:B VIN1:I VIN2:I VOUT1:O VOUT2:O VSS:B
XI1 VDD VIN2 VIN2b VSS Inverter
XI0 VDD VIN1 VIN1b VSS Inverter
XI3 VIN1b VIN2b VOUT2 VDD VSS Xor
XI2 VIN1b VIN2b VOUT1 VDD VSS Xor
.ends Std_Cell


