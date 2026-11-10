package main

import (
    "bytes"
    "crypto/sha256"
    "encoding/json"
    "fmt"
    "os"
    "path/filepath"
    "github.com/xypwn/filediver/stingray"
    "github.com/xypwn/filediver/stingray/unit/material"
)

func verifyMechAppearances(root string, resources map[stingray.FileID]resource) bool {
    raw,e:=os.ReadFile(filepath.Join(root,"reports","mech_appearances.json"))
    if os.IsNotExist(e){return false};must(e)
    var proof struct {Appearances []struct {ID string `json:"id"`; Material string `json:"native_material"`; OriginalSHA string `json:"original_material_sha256"`} `json:"appearances"`}
    must(json.Unmarshal(raw,&proof))
    expected:=map[string]string{"0x79e4b3d2da5e45e3":"23a41cbe0203470f","0xc2d449ecf7facab1":"43cdcd71bb55e04a","0x7b2326f6fd9c8069":"d02d6e1e8ea98d4f","0x35dbf54f016f3624":"9dbda1aeb7dfa9ab"}
    req(len(proof.Appearances)==4,"independent mech inventory incomplete")
    for _,row:=range proof.Appearances {
        req(expected[row.ID]==row.Material,"unknown mech appearance material");delete(expected,row.ID)
        id:=stingray.NewFileID(stingray.Hash{Value:number(row.Material)},stingray.Sum("material"));r,ok:=resources[id];req(ok,"native mech type material missing")
        original:=read(filepath.Join(root,"source","Raw","material",row.Material+".material.main"));req(fmt.Sprintf("%x",sha256.Sum256(original))==row.OriginalSHA,"mech material provenance mismatch")
        mat,e:=material.LoadMain(bytes.NewReader(r.main));must(e);req(mat.BaseMaterial.Value==0x7712ee7808e0ec50 && len(mat.Textures)==3,"mech material is not complete PBR")
        for _,tid:=range mat.Textures {_,ok:=resources[stingray.NewFileID(tid,stingray.Sum("texture"))];req(ok,"independent mech image missing")}
    }
    req(len(expected)==0,"duplicate mech appearance")
    return true
}
