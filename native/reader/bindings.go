package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"

	"github.com/xypwn/filediver/stingray"
	"github.com/xypwn/filediver/stingray/unit/material"
)

type binding struct {
	Original string `json:"original"`
	Private  string `json:"private"`
	Fixed    bool   `json:"fixed"`
	RemovedStockDecals []string `json:"removed_stock_decal_parameters"`
	ReplacementKind string `json:"replacement_kind"`
	OriginalSHA string `json:"original_sha256"`
}
type bindingProof struct {
	Textures  []binding `json:"textures"`
	Materials []binding `json:"materials"`
}

func number(v string) uint64 { n, e := strconv.ParseUint(v, 16, 64); must(e); return n }
func loadBindings(root string) bindingProof {
	var p bindingProof
	b, e := os.ReadFile(filepath.Join(root, "reports", "resource_bindings.json"))
	if os.IsNotExist(e) {
		return p
	}
	must(e)
	must(json.Unmarshal(b, &p))
	return p
}
func (p bindingProof) originalMaterial(private uint64) (binding, bool) {
	for _, m := range p.Materials {
		if number(m.Private) == private {
			return m, true
		}
	}
	return binding{}, false
}
func (p bindingProof) permits(original, private uint64) bool {
	m, ok := p.originalMaterial(private)
	return ok && number(m.Original) == original
}
func verifyColorBindings(root string, p bindingProof, resources map[stingray.FileID]resource) {
	for _, t := range p.Textures {
		old, new := number(t.Original), number(t.Private)
		req(old != new, "color texture reused a global ID")
		_, found := resources[stingray.NewFileID(stingray.Hash{Value: old}, stingray.Sum("texture"))]
		req(!found, "global color texture overwritten")
		_, found = resources[stingray.NewFileID(stingray.Hash{Value: new}, stingray.Sum("texture"))]
		req(found, "private color texture missing")
	}
	for _, m := range p.Materials {
		old, new := number(m.Original), number(m.Private)
		req(old != new, "color material reused a global ID")
		_, found := resources[stingray.NewFileID(stingray.Hash{Value: old}, stingray.Sum("material"))]
		req(!found, "global color material overwritten")
		r, found := resources[stingray.NewFileID(stingray.Hash{Value: new}, stingray.Sum("material"))]
		req(found, "private color material missing")
		original := read(filepath.Join(root, "source", "Raw", "material", fmt.Sprintf("%016x.material.main", old)))
		if m.ReplacementKind=="complete_surface_pbr" {
			req(fmt.Sprintf("%x",sha256.Sum256(original))==m.OriginalSHA,"complete material original hash mismatch")
			mat,e:=material.LoadMain(bytes.NewReader(r.main));must(e);req(mat.BaseMaterial.Value==0x7712ee7808e0ec50 && len(mat.Textures)==3,"complete replacement is not the known PBR material")
			expected:=map[uint32]bool{stingray.Sum("albedo").Thin().Value:true,stingray.Sum("normal").Thin().Value:true,stingray.Sum("mra").Thin().Value:true}
			for usage,tid:=range mat.Textures {req(expected[usage.Value],"unexpected channel in full material replacement");_,ok:=resources[stingray.NewFileID(tid,stingray.Sum("texture"))];req(ok,"complete material image missing")}
			continue
		}
		restored := append([]byte{}, r.main...)
		for _, t := range p.Textures {
			a, b := make([]byte, 8), make([]byte, 8)
			binary.LittleEndian.PutUint64(a, number(t.Private))
			binary.LittleEndian.PutUint64(b, number(t.Original))
			restored = bytes.ReplaceAll(restored, a, b)
		}
		if len(m.RemovedStockDecals)>0 {
			req(binary.LittleEndian.Uint64(original[24:])==0x43865ef50d88395e,"unexpected stock decal shader")
			nt,ns:=int(binary.LittleEndian.Uint32(original[64:])),int(binary.LittleEndian.Uint32(original[104:]));defs:=136+nt*12;base:=defs+ns*20
			allowed:=map[uint32]bool{stingray.Sum("decal_alpha_sharpness").Thin().Value:true,stingray.Sum("decal_normal_intentity").Thin().Value:true};changed:=0
			for i:=0;i<ns;i++{o:=defs+i*20;usage:=binary.LittleEndian.Uint32(original[o+8:]);if allowed[usage]{pos:=base+int(binary.LittleEndian.Uint32(original[o+12:]));req(binary.LittleEndian.Uint32(restored[pos:])==0,"stock decal parameter not zero");copy(restored[pos:pos+4],original[pos:pos+4]);changed++}}
			req(changed==2,"stock decal color and normal must both be disabled")
		}
		if m.Fixed {
			req(old == 0xc6130a81c56dff6e || old == 0x2bc2aecf229aeedb || old == 0x0f3201437c248dfc, "unexpected fixed material")
			nt, ns := binary.LittleEndian.Uint32(original[64:]), binary.LittleEndian.Uint32(original[104:])
			defs := 136 + int(nt)*12
			base := defs + int(ns)*20
			allowed := map[uint32]int{}
			for _, name := range []string{"albedo_color", "albedo_color_curavture", "emissive_color", "emissive_color_curavture"} {
				allowed[stingray.Sum(name).Thin().Value] = 12
			}
			switcher := stingray.Sum("use_uv_for_coloring").Thin().Value
			allowed[switcher] = 4
			if old == 0x0f3201437c248dfc {
				allowed = map[uint32]int{stingray.Sum("base_color").Thin().Value: 12}
			}
			for i := 0; i < int(ns); i++ {
				o := defs + i*20
				usage := binary.LittleEndian.Uint32(original[o+8:])
				if n, ok := allowed[usage]; ok {
					pos := base + int(binary.LittleEndian.Uint32(original[o+12:]))
					for j := 0; j < n; j += 4 {
						v := math.Float32frombits(binary.LittleEndian.Uint32(restored[pos+j:]))
						req(v >= 0 && v <= 1 && !math.IsNaN(float64(v)), "invalid fixed color")
					}
					if usage == switcher {
						req(binary.LittleEndian.Uint32(restored[pos:]) == 0, "fixed color switch not disabled")
					}
					copy(restored[pos:pos+n], original[pos:pos+n])
				}
			}
		}
		req(bytes.Equal(restored, original), "private material changed non-color shader data")
		a, e := material.LoadMain(bytes.NewReader(original))
		must(e)
		b, e := material.LoadMain(bytes.NewReader(r.main))
		must(e)
		req(a.BaseMaterial == b.BaseMaterial, "private color shader changed")
		for key, id := range b.Textures {
			if id != a.Textures[key] {
				_, ok := resources[stingray.NewFileID(id, stingray.Sum("texture"))]
				req(ok, "private shader texture missing")
			}
		}
	}
}

func verifyScope(root string, resources map[stingray.FileID]resource) {
	b, e := os.ReadFile(filepath.Join(root, "reports", "export_scope.json"))
	if os.IsNotExist(e) {
		return
	}
	must(e)
	var scope struct {
		Units []struct {
			ID string `json:"id"`
		} `json:"units"`
		Written []string `json:"written_units"`
		Mounted []struct {
			ID    string `json:"id"`
			Owner string `json:"owner"`
		} `json:"mounted_units"`
	}
	must(json.Unmarshal(b, &scope))
	allowed := map[uint64]bool{}
	written := map[uint64]bool{}
	for _, u := range scope.Units {
		n, e := strconv.ParseUint(u.ID[2:], 16, 64)
		must(e)
		allowed[n] = true
	}
	for _, u := range scope.Written {
		n, e := strconv.ParseUint(u[2:], 16, 64)
		must(e)
		written[n] = true
	}
	for _, u := range scope.Mounted {
		owner, e := strconv.ParseUint(u.Owner[2:], 16, 64)
		must(e)
		n, e := strconv.ParseUint(u.ID[2:], 16, 64)
		must(e)
		known := owner == 0x16474112801385b6 && n == 0x1fa1f596769225c2 || owner == 0x4e401cc877093220 && n == 0xdec89a1588c9d726
		hangarOwners:=map[uint64]uint64{
			0x0c6419d780580546:0x71e52f800636b336,0xc6442dbd3024ead9:0x71e52f800636b336,
			0x67eb4fad997eed08:0xfce722b5ef57e4b6,
			0x1edcc53dcf1c5ad2:0xebb32d4a2a4154b6,0x950e0b11f4acc10b:0xebb32d4a2a4154b6,0x52d9ea6447e77f22:0xebb32d4a2a4154b6,
			0x0978382fa80e7707:0x9a030a53a7bd6c0a,
			0xb9805dc32d0c2bab:0x4a2abce4f440fec1,0x0f48db36a62aaef7:0x4a2abce4f440fec1,0x202861e9d0530b37:0x4a2abce4f440fec1,
		}
		if expected,ok:=hangarOwners[n];ok {known=known||owner==expected}
		req(allowed[owner] && known, "Unknown mounted resource owner")
		allowed[n] = true
	}
	for id := range resources {
		if id.Type == stingray.Sum("unit") {
			req(allowed[id.Name.Value] && written[id.Name.Value], "Unit outside requested export scope")
			delete(written, id.Name.Value)
		}
	}
	req(len(written) == 0, "scope inventory mismatch")
}
