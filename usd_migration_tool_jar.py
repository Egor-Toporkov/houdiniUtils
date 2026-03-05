import os
import re
import hou
import voptoolutils


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
        # attr_wrangle_shop has cooked the geometry and stored all_groups as detail attrib
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
                mat_network, "karmamaterial", "karmamaterial", voptoolutils.KARMAMTLX_TAB_MASK, "Karma Material Builder", "kma")

            # find the mtlxstandard_surface auto-created by _setupMtlXBuilderSubnet
            mtlsurface = next(
                (n for n in mat_network.children()
                 if n.type().name() == "mtlxstandard_surface"),
                None
            )
            print(f"mtlsurface found: {mtlsurface}")

            texture_dir_ref = dir_path + "/maps"
            texture_index = str(i + 1).zfill(2)  # "01", "02", "03" ...

            # 5 mtlximage nodes:
            # textures named: ASSET_01.jpg=diffuse, _02=roughness,
            #                 _03=normal, _04=displacement, _05=ao
            # (suffix, node_name, signature, surface_input or None)
            image_configs = [
                ("01", "base_color", "color3", "base_color"),
                ("02", "roughness",  "float",  "specular_roughness"),
                ("03", "normal",     "vector3", None),   # via mtlxnormalmap
                ("04", "displacement", "float", None),   # via mtlxdisplace
                # multiplied into base_color
                ("05", "occlusion",  "float", None),
            ]

            img_nodes = {}

            for suffix, img_name, img_signature, surface_input in image_configs:
                mtlximage = mat_network.createNode("mtlximage", img_name)
                mtlximage.moveToGoodPosition()
                mtlximage.parm("signature").set(img_signature)
                img_nodes[img_name] = mtlximage

                # match file ending with _{suffix}.jpg (case-insensitive)
                texture_map = [
                    f for f in os.listdir(texture_dir_ref)
                    if f.lower().endswith(f"_{suffix}.jpg")
                ]
                print(f"  {img_name} (_{suffix}.jpg): {texture_map}")
                if texture_map:
                    mtlximage.parm("file").set(
                        texture_dir_ref + "/" + texture_map[0])
                else:
                    print(f"  {img_name} -> no texture found")

                # direct connections to mtlsurface
                if mtlsurface and surface_input:
                    mtlsurface.setInput(
                        mtlsurface.inputIndex(surface_input),
                        mtlximage,
                        mtlximage.outputIndex("out")
                    )

            # normal: mtlximage(03) -> mtlxnormalmap -> mtlsurface.normal
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

            # displacement: mtlximage(04) -> mtlxdisplace.displacement
            mtlxdisplace = next(
                (n for n in mat_network.children()
                 if n.type().name() == "mtlxdisplacement"),
                None
            )
            mtlxdisplace.setInput(
                mtlxdisplace.inputIndex("displacement"),
                img_nodes["displacement"],
                img_nodes["displacement"].outputIndex(
                    "out")
            )

            # occlusion: base_color * occlusion -> mtlsurface.base_color
            if mtlsurface and "occlusion" in img_nodes and "base_color" in img_nodes:
                multiply = mat_network.createNode(
                    "mtlxmultiply", "ao_multiply")
                multiply.moveToGoodPosition()
                # add multiply
                # multiply.parm("signature").set("color3")
                # multiply.setInput(
                #     0, img_nodes["base_color"], img_nodes["base_color"].outputIndex("out"))
                # multiply.setInput(
                #     1, img_nodes["occlusion"],  img_nodes["occlusion"].outputIndex("out"))
                # mtlsurface.setInput(
                #     mtlsurface.inputIndex("base_color"),
                #     multiply,
                #     multiply.outputIndex("out")
                # )
                mtlsurface.setInput(
                    mtlsurface.inputIndex("base_color"),
                    img_nodes["base_color"],
                    img_nodes["base_color"].outputIndex("out")
                )

        # usd rop export
        usd_rop_export = materallib_lop.createOutputNode("usd_rop")
        usd_rop_export.moveToGoodPosition()
        usd_rop_export.parm("lopoutput").set(
            dir_path + "/usd_export" + self.asset_name + ".usd")

        # layout all nodes in every container
        # /stage (LOP network)
        # hou.node(root_path).layoutChildren()
        # sopcreate SOP network (file, attribdelete, xform, wrangles, foreach, output)
        hou.node(sopcreate_lop.path() + "/sopnet/create").layoutChildren()
        # grid SOP network
        hou.node(grid_lop.path() + "/sopnet/create").layoutChildren()
        hou.node(materallib_lop.path()).layoutChildren()
        # each material subnet
        for child in materallib_lop.children():
            if child.type().name() == "subnet":
                child.layoutChildren()
