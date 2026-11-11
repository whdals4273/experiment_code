/*
- 6T1C 5X5 array driving code
- Only basic operation, including weight update and retention are implemented

by "Minseung Kang"
*/

/*  PIN_MAP_INFO

    int DFF1 = 31; //PA7 = D
    int DS = 42; //PA19
    int VH = 43; //PA20

    int FB = 22; //PB26
    int CR = 52; //PB21
    int CON = 53; //PB14
    int HL_CHOP = 2; //PB25

    int WL_0 = 25; //PD0
    int WL_1 = 26; //PD1
    int WL_2 = 27; //PD2
    int WL_3 = 28; //PD3
    int WL_4 = 29; //PD6

    int N1_0 = 51; //PC12
    int N1_1 = 50; //PC13
    int N1_2 = 49; //PC14
    int N1_3 = 48; //PC15
    int N1_4 = 47; //PC16

    int N3_0 = 46; //PC17
    int N3_1 = 45; //PC18
    int N3_2 = 44; //PC19
    int N3_3 = 41; //PC9
    int N3_4 = 40; //PC8

    int N2_0 = 39; //PC7
    int N2_1 = 38; //PC6
    int N2_2 = 37; //PC5
    int N2_3 = 36; //PC4
    int N2_4 = 35; //PC3

    int N4_0 = 34; //PC2
    int N4_1 = 33; //PC1
    int N4_2 = 7; //PC23
    int N4_3 = 6; //PC24
    int N4_4 = 5; //PC25

    int ADC_0 = A0;
    int ADC_1 = A1;
    int ADC_2 = A2;
    int ADC_3 = A3;
    int ADC_4 = A4;
*/

#include <ctype.h>
#include <stdlib.h>
#include <math.h>

const int MINUTES = 60000;  // 1 minute in ms

const int BL_READ_PINLIST[] = {7, 6, 5, 4, 3}; // BL0 ~ BL4
const int WL_READ_PINLIST[] = {1      , 1 <<  1, 1 <<  2, 1 <<  3, 1 <<  6};
const int N1_PINLIST[]      = {1 << 12, 1 << 13, 1 << 14, 1 << 15, 1 << 16};
const int N2_PINLIST[]      = {1 <<  7, 1 <<  6, 1 <<  5, 1 <<  4, 1 <<  3};
const int N3_PINLIST[]      = {1 << 17, 1 << 18, 1 << 19, 1 <<  9, 1 <<  8};
const int N4_PINLIST[]      = {1 <<  2, 1 <<  1, 1 << 23, 1 << 24, 1 << 25};
const int N1_ALL            = 1 << 12 | 1 << 13 | 1 << 14 | 1 << 15 | 1 << 16;
const int N2_ALL            = 1 <<  7 | 1 <<  6 | 1 <<  5 | 1 <<  4 | 1 <<  3;
const int N3_ALL            = 1 << 17 | 1 << 18 | 1 << 19 | 1 <<  9 | 1 <<  8;
const int N4_ALL            = 1 <<  2 | 1 <<  1 | 1 << 23 | 1 << 24 | 1 << 25;
const int WL_ALL            = 1 <<  0 | 1 <<  1 | 1 <<  2 | 1 <<  3 | 1 <<  6; // WL0 ~ WL4
