*Custom Compiler Version V-2023.12-6
*Thu Apr  9 03:20:23 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : Test
* Cell             : Transmission_Gate
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt Transmission_Gate EN ENB VDD VIN VOUT VSS
*.PININFO EN:I ENB:I VDD:B VIN:I VOUT:O VSS:B
MM11 VOUT EN VIN VSS n08 l=0.014u nf=4 m=1 nfin=5
MM12 VOUT ENB VIN VDD p08 l=0.014u nf=4 m=1 nfin=5
.ends Transmission_Gate

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
* Library          : CML_Test
* Cell             : CML
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt CML D DB DD DDB EN3 EN6 IB TX_OUTN TX_OUTP VDD VSS
*.PININFO D:I DB:I DD:I DDB:I EN3:I EN6:I IB:I TX_OUTN:O TX_OUTP:O VDD:B VSS:B
XI158 EN3B EN3 VDD DB Vn2 VSS Transmission_Gate
XI159 EN3 EN3B VDD DD Vn2 VSS Transmission_Gate
XI154 EN3B EN3 VDD D Vp2 VSS Transmission_Gate
XI156 EN3 EN3B VDD DDB Vp2 VSS Transmission_Gate
XI160 EN6 EN6B VDD DD Vn3 VSS Transmission_Gate
XI161 EN6B EN6 VDD DB Vn3 VSS Transmission_Gate
XI162 EN6B EN6 VDD D Vp3 VSS Transmission_Gate
XI163 EN6 EN6B VDD DDB Vp3 VSS Transmission_Gate
MM181 IB IB VSS VSS n08 l=0.1u nf=25 m=6 nfin=5
MMM3 net69 IB VSS VSS n08 l=100n nf=25 m=2 nfin=5
MM40 TX_OUTP Vn3 net69 VSS n08 l=0.014u nf=5 m=2 nfin=5
MM37 TX_OUTP Vn2 net64 VSS n08 l=0.014u nf=5 m=4 nfin=5
MM39 TX_OUTN Vp3 net69 VSS n08 l=0.014u nf=5 m=2 nfin=5
MM36 TX_OUTN Vp2 net64 VSS n08 l=0.014u nf=5 m=4 nfin=5
MMM1 net12 IB VSS VSS n08 l=100n nf=25 m=18 nfin=5
MMM2 net64 IB VSS VSS n08 l=100n nf=25 m=4 nfin=5
MM94 TX_OUTP DB net12 VSS n08 l=0.014u nf=5 m=10 nfin=5
MM2 TX_OUTN D net12 VSS n08 l=0.014u nf=5 m=10 nfin=5
XI164 VDD EN3 EN3B VSS Inverter
XI166 VDD EN6 EN6B VSS Inverter
rR183 VDD TX_OUTP rppoly w=0.1u l=0.372u m=1
rR182 VDD TX_OUTN rppoly w=0.1u l=0.372u m=1
.ends CML


