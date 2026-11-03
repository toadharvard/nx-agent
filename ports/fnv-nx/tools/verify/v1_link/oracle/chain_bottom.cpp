// x87 result chain, bottom: the real function returns float (st0). Hand-written, no decomp content.
struct C { float G(); float v; };
float C::G() { return v * 0.5f; }
