package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"

	"github.com/xypwn/filediver/stingray"
	"github.com/xypwn/filediver/stingray/unit"
	geometrygroup "github.com/xypwn/filediver/stingray/unit/geometry_group"
)

func verifyIndexDeclarations(layouts []unit.MeshLayout) {
	for _, layout := range layouts {
		kind := binary.LittleEndian.Uint32(layout.Unk05[:4])
		req(kind <= 1, "unsupported GPU index declaration")
		stride := uint64(2)
		if kind == 1 {
			stride = 4
		}
		req(uint64(layout.IndicesSize) == uint64(layout.NumIndices)*stride, "GPU index declaration disagrees with index bytes")
	}
}

func verifyEngineVertexRange(mesh unit.MeshInfo) {
	if len(mesh.Groups) < 2 {
		return
	}
	first := mesh.Groups[0]
	for _, group := range mesh.Groups[1:] {
		req(group.VertexOffset == first.VertexOffset && group.NumVertices == first.NumVertices,
			"split mesh groups do not share the engine vertex range")
	}
}

// Added skinned sections must address the remap entry appended for that draw.
// Rigid native meshes can legitimately reuse GroupIdx=0 and are left alone.
func verifyAddedSkinGroups(oldMeshes, newMeshes []unit.MeshInfo, maps []unit.SkeletonMap) int {
	checks := 0
	for mi, mesh := range newMeshes {
		if mesh.Header.SkeletonMapIdx < 0 || len(mesh.Groups) <= len(oldMeshes[mi].Groups) {
			continue
		}
		req(int(mesh.Header.SkeletonMapIdx) < len(maps), "missing split skeleton map")
		remaps := maps[mesh.Header.SkeletonMapIdx].RemapList
		req(len(remaps) == len(mesh.Groups), "split draw groups and bone remaps disagree")
		used := map[uint32]bool{}
		for gi, group := range mesh.Groups {
			if gi >= len(oldMeshes[mi].Groups) {
				req(group.GroupIdx == uint32(gi) && !used[group.GroupIdx], "new skinned render group reuses original group id")
				req(int(group.GroupIdx) < len(remaps) && len(remaps[group.GroupIdx]) > 0, "new skinned group lacks its bone remap")
				checks++
			}
			used[group.GroupIdx] = true
		}
	}
	return checks
}

func uvCell(raw []byte, offset, size int) [2]int {
	var result [2]int
	for i := 0; i < 2; i++ {
		var v float64
		if size == 4 {
			h := binary.LittleEndian.Uint16(raw[offset+i*2:])
			exp := (h >> 10) & 31
			mant := h & 1023
			if exp == 0 {
				v = math.Ldexp(float64(mant), -24)
			} else {
				req(exp != 31, "nonfinite UV0")
				v = math.Ldexp(1+float64(mant)/1024, int(exp)-15)
			}
			if h&0x8000 != 0 {
				v = -v
			}
		} else {
			v = float64(math.Float32frombits(binary.LittleEndian.Uint32(raw[offset+i*4:])))
		}
		req(!math.IsNaN(v) && !math.IsInf(v, 0), "nonfinite UV0")
		result[i] = int(math.Floor(v))
	}
	return result
}

func readOptional(path string) []byte {
	b, e := os.ReadFile(path)
	if os.IsNotExist(e) {
		return nil
	}
	must(e)
	return b
}

func geometryMeshes(info geometrygroup.MeshInfo) []unit.MeshInfo {
	result := make([]unit.MeshInfo, len(info.MeshHeaders))
	for i, h := range info.MeshHeaders {
		result[i] = unit.MeshInfo{Header: unit.MeshHeader{LayoutIdx: int32(h.MeshLayoutIndex), MeshName: info.MeshNames[i]}, Materials: h.Materials, Groups: h.Groups}
	}
	return result
}

func verifyMeshData(oldMeshes, newMeshes []unit.MeshInfo, oldLayouts, newLayouts []unit.MeshLayout, oldGPU, newGPU []byte) (int, int) {
	geometries, vertices := 0, 0
	for mi, mesh := range newMeshes {
		if mesh.Header.LayoutIdx < int32(len(oldLayouts)) {
			continue
		}
		verifyEngineVertexRange(mesh)
		ids := []uint32{uint32(mi)}
		a, e := unit.LoadMeshes(bytes.NewReader(oldGPU), oldMeshes, oldLayouts, ids)
		must(e)
		b, e := unit.LoadMeshes(bytes.NewReader(newGPU), newMeshes, newLayouts, ids)
		must(e)
		req(reflect.DeepEqual(trianglesGroups(a[uint32(mi)], len(oldMeshes[mi].Groups)), trianglesGroups(b[uint32(mi)], len(oldMeshes[mi].Groups))), "original shared triangle positions or winding changed")
		geometries++
		oldLayout := oldLayouts[oldMeshes[mi].Header.LayoutIdx]
		newLayout := newLayouts[mesh.Header.LayoutIdx]
		uv := func(layout unit.MeshLayout) (int, int) {
			offset := 0
			for _, item := range layout.Items[:layout.NumItems] {
				if item.Type == unit.ItemUVCoords && item.Layer == 0 {
					return offset, item.Format.Size()
				}
				offset += item.Format.Size()
			}
			panic("no UV0")
		}
		oldOffset, oldSize := uv(oldLayout)
		newOffset, newSize := uv(newLayout)
		req(newSize == 8, "shared UV0 not full precision")
		records := map[string]map[[2]int]bool{}
		for _, g := range oldMeshes[mi].Groups {
			for vi := uint32(0); vi < g.NumVertices; vi++ {
				start := int(oldLayout.VertexOffset + (g.VertexOffset+vi)*oldLayout.VertexStride)
				raw := oldGPU[start : start+int(oldLayout.VertexStride)]
				key := string(raw[:oldOffset]) + string(raw[oldOffset+oldSize:])
				if records[key] == nil {
					records[key] = map[[2]int]bool{}
				}
				records[key][uvCell(raw, oldOffset, oldSize)] = true
			}
		}
		for gi, g := range mesh.Groups {
			if gi >= len(oldMeshes[mi].Groups) {
				req(g.NumIndices > 0 && g.NumIndices%3 == 0, "invalid shared decal shell group")
				continue
			}
			for vi := uint32(0); vi < oldMeshes[mi].Groups[gi].NumVertices; vi++ {
				start := int(newLayout.VertexOffset + (g.VertexOffset+vi)*newLayout.VertexStride)
				raw := newGPU[start : start+int(newLayout.VertexStride)]
				key := string(raw[:newOffset]) + string(raw[newOffset+newSize:])
				req(records[key] != nil, "shared non-UV vertex data changed")
				req(records[key][uvCell(raw, newOffset, newSize)], "shared visibility UDIM changed")
				vertices++
			}
		}
	}
	return geometries, vertices
}

func verifySharedGeometry(root string, id stingray.FileID, old, now *unit.Info, resources map[stingray.FileID]resource) (int, int) {
	req(now.GeometryGroup.Value != 0 && now.GeometryGroup != old.GeometryGroup, "shared geometry must use a private ID")
	typ := stingray.Sum("geometry_group")
	_, overwritten := resources[stingray.NewFileID(old.GeometryGroup, typ)]
	req(!overwritten, "original shared geometry was overwritten")
	cloned, ok := resources[stingray.NewFileID(now.GeometryGroup, typ)]
	req(ok, "private geometry resource missing")
	prefix := filepath.Join(root, "source", "Raw", "geometry_group", fmt.Sprintf("%016x.geometry_group", old.GeometryGroup.Value))
	originalMain, originalGPU := read(prefix+".main"), read(prefix+".gpu")
	req(len(cloned.gpu) >= len(originalGPU) && bytes.Equal(cloned.gpu[:len(originalGPU)], originalGPU), "shared GPU original prefix changed")
	if bytes.Equal(cloned.main, originalMain) && bytes.Equal(cloned.gpu, originalGPU) {
		// A recolor-only Unit keeps a byte-identical private geometry resource.
		// No added layouts or decal draws should be required for this case.
		req(reflect.DeepEqual(old.MeshInfos, now.MeshInfos) && reflect.DeepEqual(old.MeshLayouts, now.MeshLayouts) && reflect.DeepEqual(old.SkeletonMaps, now.SkeletonMaps), "recolor-only shared mesh metadata changed")
		originalUnit := read(filepath.Join(root, "source/Raw/unit", fmt.Sprintf("%016x.unit.main", id.Name.Value)))
		changed := resources[id].main
		req(len(changed) >= len(originalUnit), "recolor-only Unit truncated")
		want := append([]byte{}, originalUnit...)
		for _, p := range [][2]int{{16, 8}, {112, 4}} {
			copy(want[p[0]:p[0]+p[1]], changed[p[0]:p[0]+p[1]])
		}
		req(bytes.Equal(want, changed[:len(want)]), "recolor-only Unit original prefix changed")
		return 0, 0
	}
	a, e := geometrygroup.LoadGeometryGroup(bytes.NewReader(originalMain))
	must(e)
	b, e := geometrygroup.LoadGeometryGroup(bytes.NewReader(cloned.main))
	must(e)
	verifyIndexDeclarations(a.MeshLayouts)
	verifyIndexDeclarations(b.MeshLayouts)
	req(a.UnkHash == b.UnkHash && len(a.MeshInfos) == len(b.MeshInfos), "shared model inventory changed")
	req(len(b.MeshLayouts) > len(a.MeshLayouts) && reflect.DeepEqual(a.MeshLayouts, b.MeshLayouts[:len(a.MeshLayouts)]), "existing shared layouts changed")
	for uid, info := range a.MeshInfos {
		if uid != id.Name {
			if !reflect.DeepEqual(info, b.MeshInfos[uid]) {
				other, exists := resources[stingray.NewFileID(uid, stingray.Sum("unit"))]
				req(exists, "an unselected model's shared mesh changed")
				selected, e := unit.LoadInfo(bytes.NewReader(other.main))
				must(e)
				req(selected.GeometryGroup == now.GeometryGroup, "changed shared model is outside the private cohort")
				originalUnit := read(filepath.Join(root, "source", "Raw", "unit", fmt.Sprintf("%016x.unit.main", uid.Value)))
				originalInfo, e := unit.LoadInfo(bytes.NewReader(originalUnit))
				must(e)
				req(originalInfo.GeometryGroup == old.GeometryGroup, "private cohort mixes unrelated original geometry")
			}
		}
	}
	oldInfo, ok := a.MeshInfos[id.Name]
	req(ok, "original GG lacks target unit")
	newInfo, ok := b.MeshInfos[id.Name]
	req(ok, "private GG lacks target unit")
	req(reflect.DeepEqual(oldInfo.MeshNames, newInfo.MeshNames), "shared mesh names or order changed")
	for _, h := range newInfo.MeshHeaders {
		for _, key := range h.Materials {
			_, ok = now.Materials[key]
			req(ok, "shared material binding missing")
		}
	}
	return verifyMeshData(geometryMeshes(oldInfo), geometryMeshes(newInfo), a.MeshLayouts, b.MeshLayouts, originalGPU, cloned.gpu)
}
