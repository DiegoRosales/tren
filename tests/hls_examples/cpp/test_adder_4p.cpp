#include "ap_int.h"
#include "hls_stream.h"

#define INPUT_BITS 10
#define OUTPUT_BITS 20

void test_adder_4p(hls::stream<ap_uint<INPUT_BITS>> &add1,
                   hls::stream<ap_uint<INPUT_BITS>> &add2,
                   hls::stream<ap_uint<INPUT_BITS>> &add3,
                   hls::stream<ap_uint<INPUT_BITS>> &add4,

                   hls::stream<ap_uint<OUTPUT_BITS>> &result) {

  result.write(add1.read() + add2.read() + add3.read() + add4.read());
}
