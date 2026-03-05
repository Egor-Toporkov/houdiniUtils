import os
import re
import hou
import voptoolutils


def normalize(s):
    """Lowercase, replace spaces with underscores, strip trailing underscores."""
    return re.sub(r'_+$', '', s.replace(' ', '_').lower())


def _token_similarity(a, b):
    """Return similarity ratio [0..1] between two strings."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def get_mat_textures(material, all_tex_files):
    """
    Match texture files to a material name.

    Strategy (in order of preference):
      1. Exact stem match after normalisation (spaces→_, lowercase, trailing _
         stripped, last suffix token stripped from filename).
      2. Fuzzy token match: split both into '_'-tokens and compute the average
         pairwise similarity of corresponding tokens.  A file qualifies if every
         token pair scores ≥ 0.75 (catches 'Maison' vs 'Mason', etc.).
         The file(s) with the highest average score are returned.

    Examples:
      material='Mason_Jar_Orange'  file='Maison_Jar_Orange_01.jpg'  fuzzy ✓
      material='Succulent_'        file='Succulent_01.jpg'           exact ✓
      material='Rope'              file='rope texture 2.png'         exact ✓
    """
    mat_norm = normalize(material)
    mat_tokens = mat_norm.split('_')

    exact = []
    scored = []  # (avg_similarity, filename)

    for f in all_tex_files:
        name_no_ext = os.path.splitext(f)[0]
        name_norm = normalize(name_no_ext)
        parts = name_norm.rsplit('_', 1)
        stem = parts[0] if len(parts) > 1 else name_norm

        if stem == mat_norm:
            exact.append(f)
            continue

        stem_tokens = stem.split('_')
        # Only consider files with the same number of tokens as the material
        if len(stem_tokens) != len(mat_tokens):
            continue

        sims = [_token_similarity(m, s)
                for m, s in zip(mat_tokens, stem_tokens)]
        if all(s >= 0.75 for s in sims):
            scored.append((sum(sims) / len(sims), f))

    if exact:
        return exact
    if not scored:
        # Final fallback: material tokens are a fuzzy-subset of file stem tokens.
        # e.g. material='Rope' (1 token) inside 'rope_texture_2' (3 tokens).
        for f in all_tex_files:
            name_no_ext = os.path.splitext(f)[0]
            stem = normalize(name_no_ext).rsplit('_', 1)
            stem = stem[0] if len(stem) > 1 else stem[0]
            stem_tokens = stem.split('_')
            if len(stem_tokens) <= len(mat_tokens):
                continue  # file has fewer tokens than material — skip
            # every material token must fuzzy-match some stem token

            def best_sim(mt):
                return max((_token_similarity(mt, st) for st in stem_tokens), default=0)
            if all(best_sim(mt) >= 0.75 for mt in mat_tokens):
                scored.append((sum(best_sim(mt)
                              for mt in mat_tokens) / len(mat_tokens), f))
    if not scored:
        return []
    best_score = max(s for s, _ in scored)
    return [f for s, f in scored if s == best_score]


class USDmigrationUtils:
    def __init__(self):
        self.asset_name = None

    def createMainTemplate(self, dir_path):
        print("Executing template structure...")

        obj_file = [file for file in os.listdir(
            dir_path) if file.endswith(".obj")][0]
        print("obj_file:", obj_file)

        self.asset_name = obj_file[:-4]
        raw_name = obj_file[:-4]
        self.asset_name = self.asset_name.replace(" ", "_")

        safe_name = re.sub(r'[^0-9a-zA-Z_]', '_', raw_name)

        # sopcreate lop
        root_path = "/stage"
        sopcreate_lop = hou.node(root_path).createNode(
            "sopcreate", safe_name)
        sopcreate_lop.moveToGoodPosition()
        sopcreate_lop.parm("enable_partitionattribs").set(0)

        # file sop
        file_sop = hou.node(sopcreate_lop.path() +
                            "/sopnet/create").createNode("file")
        file_sop.moveToGoodPosition()
        file_sop.parm("file").set(dir_path + "/" + obj_file)

        # delete attrib
        attr_del = file_sop.createOutputNode("attribdelete")
        attr_del.parm("ptdel").set("*")
        attr_del.parm("vtxdel").set("")
        attr_del.parm("primdel").set("* ^name ^path ^shop_materialpath")
        attr_del.parm("dtldel").set("*")

        # transform
        transform = attr_del.createOutputNode("xform")
        transform.parm("scale").set(0.01)

        # first wrangle: create shop_materialpath + store all_groups as detail attrib
        attr_wrangle_shop = transform.createOutputNode("attribwrangle")
        attr_wrangle_shop.parm("class").set(1)
        attr_wrangle_shop.parm("snippet").set(
            'string all_groups[] = detailintrinsic(0, "primitivegroups");\nstring keep_group = "shop_materialpath";\nforeach(string i; all_groups){\nif(inprimgroup(0, i, @primnum)){\n      if(inprimgroup(0, i, @primnum)){\n       setprimattrib(0, "shop_materialpath", @primnum, i);\n       removeprimgroup(0, i);\n      }\n}\n}\nsetdetailattrib(0, "all_groups", all_groups);')

        # for loop
        for_each_begin = attr_wrangle_shop.createOutputNode(
            "block_begin", "foreach_begin1")
        for_each_begin.parm("method").set(1)
        for_each_begin.parm("blockpath").set("../foreach_end1")
        for_each_begin.parm("createmetablock").pressButton()
        meta_node = hou.node(
            for_each_begin.parent().path() + "/foreach_begin1_metadata1"
        )
        meta_node.parm("blockpath").set("../foreach_end1")

        # attrib wrangle (VEX will be updated after we read geometry)
        attr_wrangle = for_each_begin.createOutputNode("attribwrangle")
        attr_wrangle.setInput(1, meta_node)
        attr_wrangle.parm("class").set(1)
        attr_wrangle.parm("snippet").set(
            'string assets[] = {"target"};\ns@path = "/" + assets[detail(1,"iteration")];'
        )

        # block end
        for_each_end = attr_wrangle.createOutputNode(
            "block_end", "foreach_end1")
        for_each_end.parm("itermethod").set(1)
        for_each_end.parm("method").set(1)
        for_each_end.parm("class").set(0)
        for_each_end.parm("attrib").set("shop_materialpath")
        for_each_end.parm("blockpath").set("../foreach_begin1")
        for_each_end.parm("templatepath").set("../foreach_begin1")

        matchsize_node = for_each_end.createOutputNode("matchsize")
        matchsize_node.parm("justify_y").set(1)

        # output
        output_node = matchsize_node.createOutputNode("output")

        # --- Parse all_groups from geometry BEFORE building material lop ---
        input_geo = attr_wrangle_shop.geometry()
        all_groups = list(input_geo.attribValue("all_groups"))
        print("all_groups from geometry:", all_groups)

        # Fix VEX snippet: replace "target" with actual group names
        formatted_groups = ", ".join(['"' + g + '"' for g in all_groups])
        new_vex = attr_wrangle.parm("snippet").eval().replace(
            '"target"', formatted_groups)
        attr_wrangle.parm("snippet").set(new_vex)

        # create primitive lop
        primitive_lop = hou.node(root_path).createNode("primitive")
        primitive_lop.moveToGoodPosition()
        primitive_lop.parm("primpath").set(self.asset_name)
        primitive_lop.parm("primkind").set("component")

        grid_lop = hou.node(root_path).createNode("sopcreate", "grid")
        grid_lop.moveToGoodPosition()

        grid_node = hou.node(
            grid_lop.path() + "/sopnet/create").createNode("grid")
        grid_node.moveToGoodPosition()
        grid_size = 50
        grid_node.parm("sizex").set(grid_size)
        grid_node.parm("sizey").set(grid_size)
        grid_output_node = grid_node.createOutputNode("output")

        # graph stages
        graft_stages_lop = primitive_lop.createOutputNode("graftstages")
        graft_stages_lop.moveToGoodPosition()
        graft_stages_lop.setNextInput(sopcreate_lop)
        graft_stages_lop.setNextInput(grid_lop)
        graft_stages_lop.parm("primkind").set("subcomponent")

        # material lop — now using real group names from geometry
        materials = all_groups  # <-- DYNAMIC, parsed from geometry
        materallib_lop = graft_stages_lop.createOutputNode("materiallibrary")
        materallib_lop.parm("materials").set(len(materials))

        texture_dir_ref = dir_path + "/maps"
        all_text_files = [
            f for f in os.listdir(texture_dir_ref) if not f.endswith(".rat")]
        print("all texture files:", all_text_files)

        for i, material in enumerate(materials):
            materallib_lop.parm(f"matnode{i + 1}").set(material)
            materallib_lop.parm(f"matpath{i + 1}").set(
                f"/{self.asset_name}/materials/{material}"
            )
            materallib_lop.parm(f"assign{i + 1}").set(1)
            materallib_lop.parm(f"geopath{i + 1}").set(
                f"/{self.asset_name}/{self.asset_name}/{material}"
            )

            # set mat network inside
            mat_network = hou.node(materallib_lop.path()
                                   ).createNode("subnet", material)
            mat_network.moveToGoodPosition()
            voptoolutils._setupMtlXBuilderSubnet(
                mat_network, "karmamaterial", "karmamaterial",
                voptoolutils.KARMAMTLX_TAB_MASK, "Karma Material Builder", "kma")

            mtlsurface = next(
                (n for n in mat_network.children()
                 if n.type().name() == "mtlxstandard_surface"),
                None
            )
            print(f"mtlsurface found: {mtlsurface}")

            # --- Use the module-level get_mat_textures with robust normalisation ---
            matched_textures = get_mat_textures(material, all_text_files)
            has_textures = len(matched_textures) > 0
            # print(f"  material='{material}' matched={
            # matched_textures} has_textures={has_textures}")

            if not has_textures:
                # --- WHITE MATERIAL: only mtlxconstant -> base_color ---
                print(f"  -> white material for '{material}'")
                constant = mat_network.createNode("constant", "white_color")
                constant.parm("consttype").set(19)
                constant.parm("colordefr").set(1.0)
                constant.parm("colordefg").set(1.0)
                constant.parm("colordefb").set(1.0)

                if mtlsurface:
                    mtlsurface.setInput(
                        mtlsurface.inputIndex("base_color"),
                        constant,
                        constant.outputIndex("Value")
                    )
                continue

            # --- FULL MATERIAL ---
            # (suffix, node_name, signature, surface_input or None)
            image_configs = [
                ("01", "base_color",   "color3",  "base_color"),
                ("02", "roughness",    "float",   "specular_roughness"),
                ("03", "normal",       "vector3", None),
                ("04", "displacement", "float",   None),
                ("05", "occlusion",    "color3",  None),
            ]

            img_nodes = {}

            for suffix, img_name, img_signature, surface_input in image_configs:
                mtlximage = mat_network.createNode("mtlximage", img_name)
                mtlximage.moveToGoodPosition()
                mtlximage.parm("signature").set(img_signature)
                img_nodes[img_name] = mtlximage

                # Among this material's matched files find the one with this suffix
                # Compare the last token of the normalized stem directly against suffix
                texture_map = [
                    f for f in matched_textures
                    if normalize(os.path.splitext(f)[0]).rsplit('_', 1)[-1] == suffix
                ]
                if texture_map:
                    mtlximage.parm("file").set(
                        texture_dir_ref + "/" + texture_map[0])
                else:
                    print(f"{img_name}" + f"(suffix={suffix})" +
                          "-> no texture found")

                if mtlsurface and surface_input:
                    mtlsurface.setInput(
                        mtlsurface.inputIndex(surface_input),
                        mtlximage,
                        mtlximage.outputIndex("out")
                    )

            # normal map
            if mtlsurface and "normal" in img_nodes:
                normalmap = mat_network.createNode(
                    "mtlxnormalmap", "normalmap")
                normalmap.moveToGoodPosition()
                normalmap.setInput(
                    normalmap.inputIndex("in"),
                    img_nodes["normal"],
                    img_nodes["normal"].outputIndex("out")
                )
                mtlsurface.setInput(
                    mtlsurface.inputIndex("normal"),
                    normalmap,
                    normalmap.outputIndex("out")
                )

            # displacement
            mtlxdisplace = next(
                (n for n in mat_network.children()
                 if n.type().name() == "mtlxdisplacement"),
                None
            )
            if mtlxdisplace and "displacement" in img_nodes:
                mtlxdisplace.setInput(
                    mtlxdisplace.inputIndex("displacement"),
                    img_nodes["displacement"],
                    img_nodes["displacement"].outputIndex("out")
                )

            # occlusion multiply into base_color
            if mtlsurface and "occlusion" in img_nodes and "base_color" in img_nodes:
                multiply = mat_network.createNode(
                    "mtlxmultiply", "ao_multiply")
                multiply.moveToGoodPosition()
                multiply.parm("signature").set("color3")
                multiply.setInput(
                    0, img_nodes["base_color"], img_nodes["base_color"].outputIndex("out"))
                multiply.setInput(
                    1, img_nodes["occlusion"], img_nodes["occlusion"].outputIndex("out"))
                mtlsurface.setInput(
                    mtlsurface.inputIndex("base_color"),
                    multiply,
                    multiply.outputIndex("out")
                )

        # usd rop export
        usd_rop_export = materallib_lop.createOutputNode("usd_rop")
        usd_rop_export.moveToGoodPosition()
        usd_rop_export.parm("lopoutput").set(
            dir_path + "/usd_export" + self.asset_name + ".usd")

        # layout nodes
        hou.node(sopcreate_lop.path() + "/sopnet/create").layoutChildren()
        hou.node(grid_lop.path() + "/sopnet/create").layoutChildren()
        hou.node(materallib_lop.path()).layoutChildren()
        for child in materallib_lop.children():
            if child.type().name() == "subnet":
                child.layoutChildren()
