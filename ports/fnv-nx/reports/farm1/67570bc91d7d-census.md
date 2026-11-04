# census 67570bc91d7d

- commit: `67570bc91d7df06c45447a6a991bbe5f25dfff9a`
- prepare: 329 s, census: 339 s, jobs: 4, host: 4 CPUs
- units: 2978  clean: 2907  failing: 71
- errors total: 397
- prepare log tail:

```
TYPEID1	5 files	5 edits
UNIT1	5 files	12 edits
UNITS-DEF	0 files	37 edits
UNITS-REF	0 files	82 edits
VA1	13 files	21 edits
XC1	6 files	6265 edits
XC1x	2 files	4253 edits
XINC1	49 files	135 edits
```

## Failing units (errors, first error)

- `src/havok/physics/collide/agent3/collectioncollection3/hkpcollectioncollectionagent3_gs_ehsc_nogs_noehsc_variant_rtm_variant.cpp` 63: using typedef-name '__m128' after 'union'
- `src/staging/blk_00ca0000_o2_rtm_variant.cpp` 38: expected unqualified-id before numeric constant
- `src/staging/blk_00e60000_o2_gs_nogs_rtm_variant.cpp` 22: expected constructor, destructor, or type conversion before ';' token
- `src/havok/physics/vehicle/hkpvehicleinstance_gs_nogs_rtm_variant.cpp` 19: call of overloaded '_mm_set_ss(const float&)' is ambiguous
- `src/staging/blk_00ac0000_od_ob1_variant.cpp` 18: call of overloaded 'FnvStringBase(const FnvString&)' is ambiguous
- `src/staging/blk_00b20000_od_ob1_rtm_variant.cpp` 16: expected template-name before '<' token
- `src/staging/blk_004c0000_od_decl_variant.cpp` 15: 'HTREEITEM' has not been declared
- `src/havok/physics/dynamics/world/hkpworld_rtm_variant.cpp` 13: conflicting declaration of 'hkQuadReal _mm_load_ps(const float*)' with 'C' linkage
- `src/havok/physics/utilities/visualdebugger/viewer/vehicle/hkpvehicleviewer_ehsc_noehsc_variant_rtm_variant.cpp` 13: 'hkpWorld' does not name a type
- `src/gamebryo2.2/corelibs/nianimation/nipoint3interpolator.cpp` 12: no match for 'operator=' (operand types are 'NiPoint3' and 'unsigned char [12]')
- `src/havok/physics/internal/collide/mopp/machine/hkpmoppkdopgeometriesvirtualmachine.cpp` 11: jump to label 'doSplit'
- `src/staging/blk_00e20000_od_o2_nood_variant_gs_nogs_rtm_variant.cpp` 11: expected nested-name-specifier before '_mm_load_ss'
- `src/havok/physics/utilities/visualdebugger/viewer/collide/hkpweldingviewer_gs_ehsc_nogs_noehsc_variant_rtm_variant.cpp` 10: 'hkpWorld' does not name a type
- `src/havok/common/internal/geometryprocessing/mesh/hkgpmesh.cpp` 8: call of overloaded '_mm_set1_ps(float&)' is ambiguous
- `src/speedtree/leafinfo_ob1_variant.cpp` 7: 'stdext' has not been declared
- `src/havok/common/internal/collide/gsk/hkcvxcvxdistbyheuristicsampling_rtm_variant.cpp` 6: expected constructor, destructor, or type conversion before ';' token
- `src/staging/blk_00ac0000_od_ob1_std_variant.cpp` 6: '_Mybase' in 'class std::map<std::__cxx11::basic_string<char>, std::__cxx11::basic_string<char> >' does not name a type
- `src/fallout_shared/tesobject_ob1_nogf_variant.cpp` 5: '_Allocate' was not declared in this scope; did you mean 'allocate'?
- `src/havok/physics/utilities/dynamics/inertia/hkpinertiatensorcomputer_ehsc_noehsc_variant_rtm_variant.cpp` 5: expected unqualified-id before '__extension__'
- `src/staging/blk_00b20000_od_ob1_variant_std_variant.cpp` 5: '_Buy' has not been declared in 'class std::vector<int>'
- `src/havok/common/serialize/data/util/hkdataobjectutil.cpp` 4: cannot bind non-const lvalue reference of type 'DObj&' to an rvalue of type 'DObj'
- `src/havok/physics/collide/agent/convexagent/capsulecapsule/hkpcapsulecapsuleagent.cpp` 4: request for member 'x' in '((CapsulePointVector*)this)->CapsulePointVector::m_vComponents', which is of non-class type '__m128' {aka 'float32x4_t'}
- `src/havok/physics/collide/util/hkpcollidetriangleutil_rtm_variant.cpp` 4: request for member 'x' in '((S_00cd7850_Vec*)this)->S_00cd7850_Vec::quad', which is of non-class type 'hkQuadReal' {aka 'float32x4_t'}
- `src/havok/physics/utilities/constraint/chain/hkpconstraintchainutil_gs_ehsc_nogs_noehsc_variant.cpp` 4: anonymous struct not inside named type
- `src/bsaudio/bsaudiodefines_h_unit.cpp` 3: 'm_pkHead' was not declared in this scope
- `src/fallout/ai/middlehighprocess_h_unit.cpp` 3: invalid use of incomplete type 'class QueuedFile'
- `src/gamebryo2.2/corelibs/nimain/nitexturingproperty.cpp` 3: no suitable 'operator delete' for 'fnv_00a6bb00::TexShaderMap'
- `src/havok/physics/dynamics/motion/rigid/hkpboxmotion.cpp` 3: expected '{' before '.' token
- `src/havok/physics/dynamics/motion/rigid/hkpboxmotion_rtm_variant.cpp` 3: expected '{' before '.' token
- `src/havok/physics/vehicle/hkpvehicleinstance_rtmfull_variant.cpp` 3: expected constructor, destructor, or type conversion before ';' token
- `src/speedtree/projectedshadow_ob1_variant.cpp` 3: template-id 'assign<>' for 'std::string& std::__cxx11::basic_string<char>::assign(const std::string&, unsigned int, unsigned int)' does not match any template d...
- `src/bsshader/bsshader.cpp` 2: conflicting declaration of C function 'FILE* fopen(const char*, const char*)'
- `src/fallout/ai/guardpackage.cpp` 2: 'double std::abs(float)' should have been declared inside 'std'
- `src/fallout_shared/tesanimgroup.cpp` 2: call of overloaded 'strcpy_s(char [260], int, fnv_005f3a20::NiFixedStringEx&)' is ambiguous
- `src/havok/common/internal/geometryprocessing/convexhull/hkgpconvexhull.cpp` 2: dependent-name 'AllocatedLinkedList::List<VERTEX, BASE>::Item' is parsed as a non-type, but instantiation yields a type
- `src/havok/common/internal/geometryprocessing/mesh/hkgpmesh_ehsc_noehsc_variant.cpp` 2: dependent-name 'ProbeList<V, Payload>::Item' is parsed as a non-type, but instantiation yields a type
- `src/havok/common/internal/geometryprocessing/mesh/hkgpmesh_ehsc_noehsc_variant_rtm_variant.cpp` 2: dependent-name 'ProbeList<V, Payload>::Item' is parsed as a non-type, but instantiation yields a type
- `src/havok/physics/collide/agent3/collectioncollection3/hkpcollectioncollectionagent3.cpp` 2: jump to label 'done'
- `src/havok/physics/collide/boxbox/hkpboxboxcollisiondetection_rtm_variant.cpp` 2: expected unqualified-id before '__extension__'
- `src/havok/physics/collide/query/multithreaded/collisionquery/cpu/hkpcpuworldgetclosestpointsjob_gs_ehsc_nogs_noehsc_variant.cpp` 2: conflicting declaration 'typedef struct _RTL_CRITICAL_SECTION_DEBUG RTL_CRITICAL_SECTION'
- `src/havok/physics/collide/shape/heightfield/sampledheightfield/hkpsampledheightfieldshape_rtm_variant.cpp` 2: conflicting declaration of '__m128 _mm_setr_ps(float, float, float, float)' with 'C' linkage
- `src/havok/physics/collide/util/welding/hkpmeshweldingutility_rtm_variant.cpp` 2: expected constructor, destructor, or type conversion before ';' token
- `src/havok/physics/constraintsolver/solve/hkpsolver_gs_nogs_variant.cpp` 2: jump to label 'headerNoStoreVelocity'
- `src/havok/physics/internal/collide/gjk/continuous/hkpcontinuousgsk_gs_ehsc_nogs_noehsc_variant_rtm_variant.cpp` 2: 'hkpCollidable' does not name a type
- `src/havok/physics/utilities/actions/angulardashpot/hkpangulardashpotaction_rtm_variant.cpp` 2: expected unqualified-id before '__extension__'
- `src/havok/physics/utilities/visualdebugger/viewer/dynamics/constraint/drawer/hkpwheeldrawer_rtm_variant.cpp` 2: request for member 'y' in 'b->Body::limitsA0', which is of non-class type '__m128' {aka 'float32x4_t'}
- `src/speedtree/indexedgeometry_ob1_variant.cpp` 2: 'aux' was not declared in this scope; did you mean 'Aux'?
- `src/staging/blk_00cc0000_od_o2_nood_variant_rtm_variant.cpp` 2: lvalue required as unary '&' operand
- `src/gamebryo2.2/corelibs/nimain/niintegersextradata_ob1_variant.cpp` 1: cannot bind non-const lvalue reference of type 'NiIntStreamEntryC&' to an rvalue of type 'NiIntStreamEntryC'
- `src/gamebryo2.2/corelibs/nimain/nitset_h_unit.cpp` 1: specialization of 'void NiTSet<T, Allocator>::Realloc(unsigned int) [with T = NiPointer<NiMaterialResource>; Allocator = NiTNewInterface<NiPointer<NiMaterialRes...
- `src/gamebryo2.2/corelibs/nimain/niviewerstrings.cpp` 1: call of overloaded 'NiSprintf(char*&, long unsigned int, const char [8], const char*&, int&)' is ambiguous
- `src/gamebryo2.2/corelibs/niparticle/nipsysdata_o2_variant.cpp` 1: definition of implicitly-declared 'Point3ParameterData::Point3ParameterData()'
- `src/havok/common/base/container/array/hkarrayutil_gs_ehsc_nogs_noehsc_variant_rtm_variant.cpp` 1: jump to label 'advance'
- `src/havok/common/internal/convexhull/hkplaneequationutil_gs_nogs_rtm_variant.cpp` 1: '_MSC_FULL_VER' was not declared in this scope
- `src/havok/common/internal/misc/hksystemtime_gs_nogs_rtm_variant.cpp` 1: jump to label 'next'
- `src/havok/physics/collide/agent3/machine/nn/hkpagentnnmachine_rtm_variant.cpp` 1: jump to label 'PROCESS_AT_T1'
- `src/havok/physics/collide/query/collector/kdtreecollector/hkpkdtreecollidableraycaster.cpp` 1: no suitable 'operator delete' for 'FUN_00d979a0'
- `src/havok/physics/collide/query/collector/kdtreecollector/hkpkdtreecollidableraycaster_gs_ehsc_nogs_noehsc_variant.cpp` 1: no suitable 'operator delete' for 'FUN_00d979a0'
- `src/havok/physics/collide/shape/convex/sphere/hkpsphereshape_rtm_variant.cpp` 1: jump to label 'miss'
- `src/havok/physics/constraintsolver/solve/hkpsolver_rtm_variant.cpp` 1: could not convert '((const Step_Vector4*)this)->Step_Vector4::m_vComponents' from 'const Step_PlainQuad' to 'Step_SimdReal'
- `src/havok/physics/dynamics/world/simulation/continuous/hkpcontinuoussimulation_ehsc_noehsc_variant.cpp` 1: 'hkpWorld' does not name a type; did you mean 'World'?
- `src/havok/physics/internal/collide/mopp/machine/hkpmoppmodifyvirtualmachine.cpp` 1: jump to label 'reject'
- `src/havok/physics/utilities/dynamics/savecontactpoints/hkpsavecontactpointsutil.cpp` 1: binding reference of type 'Struct0x20* const&' to 'Struct0x20* __restrict__' discards qualifiers
- `src/staging/blk_00620000_o2_od_oy_noo2_variant.cpp` 1: lvalue required as unary '&' operand
- `src/staging/blk_00a20000_o2_noehsc_variant.cpp` 1: no suitable 'operator delete []' for 'FUN_00a2bdb0'
- `src/staging/blk_00b00000_od.cpp` 1: too few arguments to function 'void* FindFirstFileA(LPCSTR, LPWIN32_FIND_DATAA)'
- `src/staging/blk_00c60000_od_o2_nood_variant_rtm_variant.cpp` 1: expected constructor, destructor, or type conversion before ';' token
- `src/staging/blk_00d40000_o2_rtm_variant.cpp` 1: expected constructor, destructor, or type conversion before ';' token
- `src/staging/blk_00d60000_od_o2_nood_variant_rtm_variant.cpp` 1: request for member 'x' in '(float*)(& quat.hkQuaternion::m_vComponents)', which is of non-class type 'float*'
- `src/staging/blk_00d80000_o2_gs_nogs_rtm_variant.cpp` 1: expected nested-name-specifier before '__m128'
- `src/staging/blk_00e60000_o2_rtm_variant.cpp` 1: request for member 'lanes' in '* dst', which is of non-class type '__m128' {aka 'float32x4_t'}
