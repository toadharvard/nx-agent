struct VB { int x; VB(); virtual ~VB(); virtual int f(); };
struct D1 : virtual VB { D1(); ~D1(); int y; };
struct D2 : D1 { D2(); ~D2(); int z; };
D1::D1() { y = 1; }
D1::~D1() { y = 2; }
D2::D2() { z = x; }
D2::~D2() { z = 3; }
