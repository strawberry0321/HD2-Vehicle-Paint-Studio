package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"github.com/xypwn/filediver/stingray"
	"github.com/xypwn/filediver/stingray/unit"
	gg "github.com/xypwn/filediver/stingray/unit/geometry_group"
	"path/filepath"
	"reflect"
)

func verifyLocalShared(root string, id stingray.FileID, old, now *unit.Info, r resource) (int, int) {
	prefix := filepath.Join(root, "source/Raw/geometry_group", fmt.Sprintf("%016x.geometry_group", old.GeometryGroup.Value))
	geometry, e := gg.LoadGeometryGroup(bytes.NewReader(read(prefix + ".main")))
	must(e)
	gpu := read(prefix + ".gpu")
	entry, ok := geometry.MeshInfos[id.Name]
	req(ok, "local conversion source GG entry missing")
	names := map[stingray.ThinHash]gg.MeshHeader{}
	for i, name := range entry.MeshNames {
		names[name] = entry.MeshHeaders[i]
	}
	expected := append([]unit.MeshInfo{}, old.MeshInfos...)
	indices := []uint32{}
	vertices := 0
	for mi := range expected {
		shared, ok := names[expected[mi].Header.MeshName]
		req(ok, "local conversion active mesh missing")
		expected[mi].Header.LayoutIdx = int32(shared.MeshLayoutIndex)
		expected[mi].Materials = shared.Materials
		expected[mi].Groups = shared.Groups
		req(reflect.DeepEqual(expected[mi].Materials, now.MeshInfos[mi].Materials), "local conversion material slots changed")
		req(len(shared.Groups) == len(now.MeshInfos[mi].Groups), "local conversion group count changed")
		a := geometry.MeshLayouts[shared.MeshLayoutIndex]
		b := now.MeshLayouts[now.MeshInfos[mi].Header.LayoutIdx]
		req(a.VertexStride == b.VertexStride && reflect.DeepEqual(a.Items, b.Items) && a.NumItems == b.NumItems, "local conversion vertex layout changed")
		aStep := a.IndicesSize / a.NumIndices
		bStep := b.IndicesSize / b.NumIndices
		req(aStep == bStep && (aStep == 2 || aStep == 4), "local conversion index width")
		for gi, x := range shared.Groups {
			y := now.MeshInfos[mi].Groups[gi]
			req(x.MaterialIdx == y.MaterialIdx && x.NumVertices == y.NumVertices && x.NumIndices == y.NumIndices && x.GroupIdx == y.GroupIdx, "local conversion draw attributes changed")
			av := int(a.VertexOffset + x.VertexOffset*a.VertexStride)
			bv := int(b.VertexOffset + y.VertexOffset*b.VertexStride)
			nv := int(x.NumVertices * a.VertexStride)
			ai := int(a.IndexOffset + x.IndexOffset*aStep)
			bi := int(b.IndexOffset + y.IndexOffset*bStep)
			ni := int(x.NumIndices * aStep)
			req(av+nv <= len(gpu) && bv+nv <= len(r.gpu) && ai+ni <= len(gpu) && bi+ni <= len(r.gpu), "local conversion buffer range")
			req(bytes.Equal(gpu[av:av+nv], r.gpu[bv:bv+nv]), "local conversion vertex bytes changed")
			req(bytes.Equal(gpu[ai:ai+ni], r.gpu[bi:bi+ni]), "local conversion index bytes changed")
			vertices += int(x.NumVertices)
		}
		indices = append(indices, uint32(mi))
	}
	a, e := unit.LoadMeshes(bytes.NewReader(gpu), expected, geometry.MeshLayouts, indices)
	must(e)
	b, e := unit.LoadMeshes(bytes.NewReader(r.gpu), now.MeshInfos, now.MeshLayouts, indices)
	must(e)
	for index, before := range a {
		after, ok := b[index]
		req(ok, "local conversion mesh absent")
		req(reflect.DeepEqual(before.Positions, after.Positions) && reflect.DeepEqual(before.Indices, after.Indices) && reflect.DeepEqual(before.UVCoords, after.UVCoords), "local conversion decoded geometry/UVs changed")
	}
	// The original prefix also retains every unparsed native metadata field.
	original := read(filepath.Join(root, "source/Raw/unit", fmt.Sprintf("%016x.unit.main", id.Name.Value)))
	want := append([]byte{}, original...)
	for _, p := range [][2]int{{16, 8}, {92, 4}, {100, 4}, {112, 4}} {
		copy(want[p[0]:p[0]+p[1]], r.main[p[0]:p[0]+p[1]])
	}
	req(len(r.main) >= len(want) && bytes.Equal(want, r.main[:len(want)]), "local conversion original metadata modified")
	req(binary.LittleEndian.Uint64(r.main[16:]) == 0, "local conversion still references shared geometry")
	return len(a), vertices
}
