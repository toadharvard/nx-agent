"""sm_classes.py - failure CLASSES: one class = one root cause in the translator (or its layout contract), keyed by the first
diagnostic of the failing stage.  Every class is a work item for a fix task.  Unknown messages fall into 'unclassified: <msg>'."""
import re
USAGES = "position blendweight blendindices normal psize texcoord tangent binormal tessfactor positiont color fog depth sample".split()
INPUT = "sampler type input: ps_1_x sampler type is the bound texture's, the translator has no input for it (CTAB-less default is a guess)"
RULES = [
 (r"no (attribute|varying) location for usage (\d+) index (\d+)", lambda m, r: ("layout: no %s location for usage %s" % (m.group(1), USAGES[int(m.group(2))])) if m.group(3) == "0" else "layout: %s location table stops below index 15 (usage index > 0)" % m.group(1)),
 (r"(\w+) unimplemented in glsl profile", lambda m, r: "emitter missing: " + m.group(1).lower().replace("texregnar", "texreg2ar").replace("texregngb", "texreg2gb").replace("texregnrgb", "texreg2rgb").replace("texdpn", "texdp3").replace("texmnxndepth", "texm3x2depth")),
 (r"TEXLDP on a cubemap", lambda m, r: "glsl: texldp with cube sampler"),
 (r"(textureCubeGrad|textureNDGrad|texture3DGrad)", lambda m, r: "glsl: texldd with cube/volume sampler (Grad function missing)"),
 (r"if-statement condition must be scalar boolean", lambda m, r: "glsl: predicate p0 used as vector condition (if/callnz/breakp pred)"),
 (r"`(vs|ps)_aL' undeclared", lambda m, r: "glsl: aL read as ALU source operand (declaration missing)"),
 (r"value of type vec\d cannot be assigned to variable of type float", lambda m, r: "glsl: vec4 result written to scalar sink (oFog/oPts)"),
 (r"syntax error, unexpected '\)'", lambda m, r: "glsl: %s emitter syntax error" % r["feature"].split("@")[0]),
 (r"no function with name '(vs|ps)_l\d+'", lambda m, r: "glsl: forward call to a later-defined subroutine has no prototype"),
 (r"TEXLD src\d must have no modifiers", lambda m, r: "front end: ps_3_0 texld coordinate with source modifier refused"),
 (r"Reserved bits? #?\d* in source token", lambda m, r: "front end: one-source sgn (vs_3_0 form) refused"),
 (r"SINCOS write mask must be", lambda m, r: "front end: sincos .xyz write mask refused (MS vs masking page allows it, sincos page does not)"),
 (r"M\dX\d writemask", lambda m, r: "front end: matrix-op write mask rule differs from the table"),
 (r"TEXM\dX\d needs a", lambda m, r: INPUT),
 (r"Shader Model 1\.0", lambda m, r: "front end: vs_1_0/ps_1_0 version token refused (undetermined creatability, included as 1_1 grammar)"),
 (r"label register number must be", lambda m, r: "front end: label index above 15 refused (sm3 allows 0..2047)"),
 (r"FRC writemask must be", lambda m, r: "front end: frc .x write mask refused in vs_1_1"),
 (r"(FRC|SINCOS) destination can't use saturate", lambda m, r: "front end: _sat on frc/sincos refused (ps)"),
 (r"reserved bits in DCL dword", lambda m, r: "front end: vs_3_0 dcl_cube/dcl_volume sampler refused (reserved-bit mask)"),
 (r"instruction token high bit", lambda m, r: "front end: instruction token high bit refused (%s)" % r["feature"]),
 (r"(POW|LOG|LOGP|RCP|RSQ|EXP|EXPP|SINCOS|DPNADD) .*replicate swizzle", lambda m, r: "generator: replicate swizzle missing"),
 (r"CND src must be", lambda m, r: "generator: cnd src0 rule"),
 (r"used uninitialized|never written", lambda m, r: "generator: uninitialised register in test shader"),
]
def classify(row, status, detail):
    if row["model"] in ("vs_1_0", "ps_1_0"): return "front end: vs_1_0/ps_1_0 version token refused (undetermined creatability, included as 1_1 grammar)"
    if status == "encode_fail": return "generator: cannot encode (" + detail[:60] + ")"
    for pat, f in RULES:
        m = re.search(pat, detail)
        if m: return f(m, row)
    return "unclassified: " + re.sub(r"\d+", "N", detail)[:90]
