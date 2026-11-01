#include "ap_int.h"
#include "hls_stream.h"

#define INPUT_BITS 2
#define OUTPUT_BITS 4

void test_adder_2p(hls::stream<ap_uint<INPUT_BITS>> &add1,
                   hls::stream<ap_uint<INPUT_BITS>> &add2,

                   hls::stream<ap_uint<OUTPUT_BITS>> &result) {

  result.write(add1.read() + add2.read());
}
