package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"github.com/xypwn/filediver/dds"
	"github.com/xypwn/filediver/stingray"
	"github.com/xypwn/filediver/stingray/unit"
	"github.com/xypwn/filediver/stingray/unit/material"
	"math"
	"os"
	"path/filepath"
	"reflect"
)

func must(e error) {
	if e != nil {
		panic(e)
	}
}
func req(v bool, s string) {
	if !v {
		panic(s)
	}
}
func read(p string) []byte { b, e := os.ReadFile(p); must(e); return b }

type resource struct{ main, stream, gpu []byte }

func triangles(m unit.Mesh) map[[9]uint32]int {
	return trianglesGroups(m, -1)
}

// Decal shell groups are appended after the original draw groups and are
// deliberately offset by a fraction of a millimetre. Only the original groups
// must stay bit-exact, so callers pass their count here.
func trianglesGroups(m unit.Mesh, limit int) map[[9]uint32]int {
	r := map[[9]uint32]int{}
	for gi, indices := range m.Indices {
		if limit >= 0 && gi >= limit {
			break
		}
		for i := 0; i < len(indices); i += 3 {
			var key [9]uint32
			for k := 0; k < 3; k++ {
				p := m.Positions[indices[i+k]]
				for j := 0; j < 3; j++ {
					key[k*3+j] = math.Float32bits(p[j])
				}
			}
			r[key]++
		}
	}
	return r
}
func main() {
	root := os.Args[1]
	verifiedDDS, cachePath, readerHash := loadDDSCache()
	bindings := loadBindings(root)
	base := filepath.Join(root, "package", "9ba626afa44a3aa3.patch_0")
	main := read(base)
	stream := read(base + ".stream")
	gpu := read(base + ".gpu_resources")
	ar, e := stingray.LoadArchive("9ba626afa44a3aa3", bytes.NewReader(main))
	must(e)
	// The game loader follows TypeData.Count as contiguous blocks. Filediver
	// permits interleaved per-file records, so explicitly verify this contract.
	blockCursor := 0
	for _, typ := range ar.Types {
		for n := uint32(0); n < typ.Count; n++ {
			req(blockCursor < len(ar.Files), "type block exceeds file table")
			req(ar.Files[blockCursor].ID.Type == typ.Name, "interleaved resource types: engine loader requires contiguous type blocks")
			blockCursor++
		}
	}
	req(blockCursor == len(ar.Files), "type counts do not cover file table")
	resources := map[stingray.FileID]resource{}
	counts := map[stingray.Hash]int{}
	decoded, nt, nm, nu, geomChecks, vertexChecks, sharedChecks, skinGroupChecks := 0, 0, 0, 0, 0, 0, 0, 0
	reusedMips, localSharedChecks := 0, 0
	var mats []*material.Material
	for _, f := range ar.Files {
		req(f.ID.Type == stingray.Sum("texture") || f.ID.Type == stingray.Sum("material") || f.ID.Type == stingray.Sum("unit") || f.ID.Type == stingray.Sum("geometry_group"), "unexpected resource type")
		_, has := resources[f.ID]
		req(!has, "duplicate ID")
		buffers := [][]byte{main, stream, gpu}
		parts := make([][]byte, 3)
		for i := 0; i < 3; i++ {
			a := f.Offsets[i]
			b := a + uint64(f.Sizes[i])
			req(b <= uint64(len(buffers[i])), "resource bounds")
			parts[i] = buffers[i][a:b]
		}
		resources[f.ID] = resource{parts[0], parts[1], parts[2]}
		counts[f.ID.Type]++
		if f.ID.Type == stingray.Sum("texture") {
			nt++
			req(len(parts[0]) == 340, "texture header size")
			body := parts[2]
			if len(parts[1]) > 0 {
				body = parts[1]
				if len(parts[2]) > 0 {
					req(bytes.Equal(parts[2], body[len(body)-len(parts[2]):]), "resident mip mismatch")
				}
			}
			data := append(append([]byte{}, parts[0][192:]...), body...)
			req(string(data[:4]) == "DDS ", "DDS signature")
			w := int(binary.LittleEndian.Uint32(data[16:]))
			h := int(binary.LittleEndian.Uint32(data[12:]))
			mips := int(binary.LittleEndian.Uint32(data[28:]))
			format := binary.LittleEndian.Uint32(data[128:])
			req(w > 0 && h > 0 && mips > 0 && mips <= 32, "DDS dimensions/mip count")
			digest := fmt.Sprintf("%x", sha256.Sum256(data))
			expectedMips := mips
			if format == 10 {
				expectedMips = 0
			}
			prior, known := verifiedDDS[digest]
			known = known && prior == expectedMips
			off := 148
			for mip := 0; mip < mips; mip++ {
				mw := max(1, w>>mip)
				mh := max(1, h>>mip)
				bs := 16
				if format == 71 || format == 72 {
					bs = 8
				}
				size := max(1, (mw+3)/4) * max(1, (mh+3)/4) * bs
				if format == 10 {
					size = mw * mh * 8
				}
				req(off+size <= len(data), "truncated mip")
				if format != 10 {
					if known {
						reusedMips++
					} else {
						hdr := append([]byte{}, data[:148]...)
						binary.LittleEndian.PutUint32(hdr[12:], uint32(mh))
						binary.LittleEndian.PutUint32(hdr[16:], uint32(mw))
						binary.LittleEndian.PutUint32(hdr[28:], 1)
						if format == 99 {
							binary.LittleEndian.PutUint32(hdr[128:], 98)
						}
						im, e := dds.Decode(bytes.NewReader(append(hdr, data[off:off+size]...)), true)
						must(e)
						req(im.Bounds().Dx() == mw && im.Bounds().Dy() == mh, "mip dimensions")
						decoded++
					}
				}
				off += size
			}
			req(off == len(data), "DDS length")
			verifiedDDS[digest] = expectedMips
		} else if f.ID.Type == stingray.Sum("material") {
			nm++
			m, e := material.LoadMain(bytes.NewReader(parts[0]))
			must(e)
			if _, cloned := bindings.originalMaterial(f.ID.Name.Value); cloned {
				// Verified byte-for-byte against its protected original below.
			} else if m.BaseMaterial.Value == 0x55f47c155e2a16fe || m.BaseMaterial.Value == 0x7712ee7808e0ec50 {
				req(len(m.Textures) == 3, "PBR texture count")
				mats = append(mats, m)
			} else {
				req(f.ID.Name.Value == 0xc6130a81c56dff6e && m.BaseMaterial.Value == 0xf8074ddca3c2ac58, "unexpected fixed-color material")
			}
		}
	}
	for _, typ := range ar.Types {
		req(counts[typ.Name] == int(typ.Count), "type count")
	}
	verifyColorBindings(root, bindings, resources)
	verifyScope(root, resources)
	independentMechs := verifyMechAppearances(root, resources)
	for _, m := range mats {
		for _, id := range m.Textures {
			_, has := resources[stingray.NewFileID(id, stingray.Sum("texture"))]
			req(has, "missing PBR image")
		}
	}
	for id, r := range resources {
		if id.Type != stingray.Sum("unit") {
			continue
		}
		nu++
		origMain := read(filepath.Join(root, "source", "Raw", "unit", fmt.Sprintf("%016x.unit.main", id.Name.Value)))
		origGPU := readOptional(filepath.Join(root, "source", "Raw", "unit", fmt.Sprintf("%016x.unit.gpu", id.Name.Value)))
		req(len(r.stream) == 0, "unexpected Unit stream")
		req(len(r.gpu) >= len(origGPU) && bytes.Equal(r.gpu[:len(origGPU)], origGPU), "original GPU bytes changed")
		old, e := unit.LoadInfo(bytes.NewReader(origMain))
		must(e)
		now, e := unit.LoadInfo(bytes.NewReader(r.main))
		must(e)
		verifyIndexDeclarations(old.MeshLayouts)
		verifyIndexDeclarations(now.MeshLayouts)
		req(now.BonesHash == old.BonesHash && now.StateMachine == old.StateMachine, "rig or state changed")
		req(reflect.DeepEqual(now.Bones, old.Bones) && reflect.DeepEqual(now.LODGroups, old.LODGroups), "bones or LOD structure changed")
		req(reflect.DeepEqual(now.GroupBones, old.GroupBones), "mesh GroupBones table changed")
		req(len(now.MeshInfos) == len(old.MeshInfos), "mesh count changed")
		req(reflect.DeepEqual(now.SkeletonMaps[:len(old.SkeletonMaps)], old.SkeletonMaps), "existing skeleton maps changed")
		skinGroupChecks += verifyAddedSkinGroups(old.MeshInfos, now.MeshInfos, now.SkeletonMaps)
		for key, mid := range now.Materials {
			if oldMid, ok := old.Materials[key]; ok {
				req(oldMid == mid || bindings.permits(oldMid.Value, mid.Value), "original material mapping changed without a private clone")
			} else {
				_, ok = resources[stingray.NewFileID(mid, stingray.Sum("material"))]
				req(ok, "missing private material")
			}
		}
		if old.GeometryGroup.Value != 0 {
			if now.GeometryGroup.Value == 0 {
				g, v := verifyLocalShared(root, id, old, now, r)
				geomChecks += g
				vertexChecks += v
				localSharedChecks++
				continue
			}
			if now.GeometryGroup == old.GeometryGroup {
				req(reflect.DeepEqual(old.MeshInfos, now.MeshInfos), "unpainted shared Unit mesh changed")
				continue
			}
			g, v := verifySharedGeometry(root, id, old, now, resources)
			geomChecks += g
			vertexChecks += v
			sharedChecks++
			continue
		}
		req(now.GeometryGroup.Value == 0, "unexpected geometry reference")
		for mi, mesh := range now.MeshInfos {
			if mesh.Header.LayoutIdx < int32(len(old.MeshLayouts)) {
				continue
			}
			if independentMechs && id.Name.Value == 0x79e4b3d2da5e45e3 {
				req(len(mesh.Groups) == len(old.MeshInfos[mi].Groups), "mech overlay groups must not be added")
				for _, slot := range mesh.Materials {
					req(slot.Value == 0x546657cb || slot.Value == 0x9685707c || slot.Value == 0xb4d0b3c9, "unknown mech atlas slot")
				}
			}
			verifyEngineVertexRange(mesh)
			ids := []uint32{uint32(mi)}
			a, e := unit.LoadMeshes(bytes.NewReader(origGPU), old.MeshInfos, old.MeshLayouts, ids)
			must(e)
			b, e := unit.LoadMeshes(bytes.NewReader(r.gpu), now.MeshInfos, now.MeshLayouts, ids)
			must(e)
			req(reflect.DeepEqual(trianglesGroups(a[uint32(mi)], len(old.MeshInfos[mi].Groups)), trianglesGroups(b[uint32(mi)], len(old.MeshInfos[mi].Groups))), "original triangle positions or winding changed")
			geomChecks++
			oldLayout := old.MeshLayouts[old.MeshInfos[mi].Header.LayoutIdx]
			newLayout := now.MeshLayouts[mesh.Header.LayoutIdx]
			oldOffset, newOffset, oldSize, newSize := 0, 0, 0, 0
			for _, item := range oldLayout.Items[:oldLayout.NumItems] {
				if item.Type == unit.ItemUVCoords && item.Layer == 0 {
					oldSize = item.Format.Size()
					break
				}
				oldOffset += item.Format.Size()
			}
			for _, item := range newLayout.Items[:newLayout.NumItems] {
				if item.Type == unit.ItemUVCoords && item.Layer == 0 {
					newSize = item.Format.Size()
					break
				}
				newOffset += item.Format.Size()
			}
			req(newSize == 8, "UV0 not full precision")
			records := map[string]map[[2]int]bool{}
			for _, group := range old.MeshInfos[mi].Groups {
				for vi := uint32(0); vi < group.NumVertices; vi++ {
					start := int(oldLayout.VertexOffset + (group.VertexOffset+vi)*oldLayout.VertexStride)
					raw := origGPU[start : start+int(oldLayout.VertexStride)]
					key := string(raw[:oldOffset]) + string(raw[oldOffset+oldSize:])
					if records[key] == nil {
						records[key] = map[[2]int]bool{}
					}
					records[key][uvCell(raw, oldOffset, oldSize)] = true
				}
			}
			for gi, group := range mesh.Groups {
				if gi >= len(old.MeshInfos[mi].Groups) {
					req(group.NumIndices > 0 && group.NumIndices%3 == 0, "invalid decal shell group")
					continue
				}
				// Original groups now share the whole vertex buffer and the
				// declared range includes the appended shell vertices. Only
				// the source group's original vertex count is bit-exact.
				for vi := uint32(0); vi < old.MeshInfos[mi].Groups[gi].NumVertices; vi++ {
					start := int(newLayout.VertexOffset + (group.VertexOffset+vi)*newLayout.VertexStride)
					raw := r.gpu[start : start+int(newLayout.VertexStride)]
					key := string(raw[:newOffset]) + string(raw[newOffset+newSize:])
					req(records[key] != nil, "non-UV vertex data changed")
					req(records[key][uvCell(raw, newOffset, newSize)], "visibility UDIM changed")
					vertexChecks++
				}
			}
		}
		for key, mid := range now.Materials {
			if _, ok := old.Materials[key]; !ok {
				_, ok = resources[stingray.NewFileID(mid, stingray.Sum("material"))]
				req(ok, "missing face material")
			}
		}
	}
	saveDDSCache(cachePath, readerHash, verifiedDDS)
	result := map[string]any{"status": "passed", "reader": "Filediver archive / material / DDS / native Unit", "textures": nt, "materials": nm, "units": nu, "private_geometry_groups_verified": sharedChecks, "local_shared_units_verified": localSharedChecks, "added_skin_draw_groups_verified": skinGroupChecks, "ordinary_mips_decoded": decoded, "ordinary_mips_verified_by_identical_dds_hash": reusedMips, "modified_meshes_verified": geomChecks, "vertex_records_non_uv_bytes_verified": vertexChecks, "triangle_positions_winding_preserved": true, "bones_lods_and_existing_skeleton_maps_preserved": true, "original_gpu_prefix_preserved": true}
	b, e := json.MarshalIndent(result, "", "  ")
	must(e)
	must(os.MkdirAll(filepath.Join(root, "reports"), 0755))
	must(os.WriteFile(filepath.Join(root, "reports", "independent_patch_audit.json"), b, 0644))
	fmt.Println(string(b))
}
