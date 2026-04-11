*Custom Compiler Version V-2023.12-6
*Sat Apr 11 19:46:32 2026

*.SCALE METER
*.LDD

********************************************************************************
* Library          : comp_fortest
* Cell             : comparator
* View             : schematic
* View Search List : auCdl schematic
* View Stop List   : auCdl
********************************************************************************
.subckt comparator CLK GND VDD VINN VINP VOUTN VOUTP
*.PININFO CLK:I GND:B VDD:B VINN:I VINP:I VOUTN:O VOUTP:O
MM5 VOUTP VOUTN VDD VDD p08 l=0.014u nf=1 m=4 nfin=2
MM4 VOUTN VOUTP VDD VDD p08 l=0.014u nf=1 m=4 nfin=2
MM3 VY CLK VDD VDD p08 l=0.014u nf=1 m=8 nfin=2
MM2 VOUTP CLK VDD VDD p08 l=0.014u nf=1 m=8 nfin=2
MM1 VOUTN CLK VDD VDD p08 l=0.014u nf=1 m=8 nfin=2
MM0 VX CLK VDD VDD p08 l=0.014u nf=1 m=8 nfin=2
MM9<7> VY VINN net2<3> GND n08 l=28n nf=1 nfin=7
MM9<6> VY VINN net2<2> GND n08 l=28n nf=1 nfin=7
MM9<5> VY VINN net2<1> GND n08 l=28n nf=1 nfin=7
MM9<4> VY VINN net2<0> GND n08 l=28n nf=1 nfin=7
MM9<3> VY VINN net2<3> GND n08 l=28n nf=1 nfin=7
MM9<2> VY VINN net2<2> GND n08 l=28n nf=1 nfin=7
MM9<1> VY VINN net2<1> GND n08 l=28n nf=1 nfin=7
MM9<0> VY VINN net2<0> GND n08 l=28n nf=1 nfin=7
MM8<7> VX VINP net2<3> GND n08 l=28n nf=1 nfin=7
MM8<6> VX VINP net2<2> GND n08 l=28n nf=1 nfin=7
MM8<5> VX VINP net2<1> GND n08 l=28n nf=1 nfin=7
MM8<4> VX VINP net2<0> GND n08 l=28n nf=1 nfin=7
MM8<3> VX VINP net2<3> GND n08 l=28n nf=1 nfin=7
MM8<2> VX VINP net2<2> GND n08 l=28n nf=1 nfin=7
MM8<1> VX VINP net2<1> GND n08 l=28n nf=1 nfin=7
MM8<0> VX VINP net2<0> GND n08 l=28n nf=1 nfin=7
MM10<3> net2<3> CLK GND GND n08 l=0.014u nf=1 nfin=2
MM10<2> net2<2> CLK GND GND n08 l=0.014u nf=1 nfin=2
MM10<1> net2<1> CLK GND GND n08 l=0.014u nf=1 nfin=2
MM10<0> net2<0> CLK GND GND n08 l=0.014u nf=1 nfin=2
MM7 VOUTN VOUTP VX GND n08 l=0.014u nf=1 nfin=2
MM6 VOUTP VOUTN VY GND n08 l=0.014u nf=1 nfin=2
.ends comparator


