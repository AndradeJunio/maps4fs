"""This module contains the Config class for map settings and configuration."""

from __future__ import annotations

import json
import os
from random import choice, randint, uniform
from typing import Generator
from xml.etree import ElementTree as ET

import cv2
import numpy as np

from maps4fs.generator.component import Component
from maps4fs.generator.texture import Texture

DISPLACEMENT_LAYER_SIZE_FOR_BIG_MAPS = 32768
DEFAULT_MAX_LOD_DISTANCE = 10000
DEFAULT_MAX_LOD_OCCLUDER_DISTANCE = 10000
NODE_ID_STARTING_VALUE = 2000
SPLINES_NODE_ID_STARTING_VALUE = 5000
TREE_NODE_ID_STARTING_VALUE = 10000
DEFAULT_FOREST_DENSITY = 10


# pylint: disable=R0903
class I3d(Component):
    """Component for map i3d file settings and configuration.

    Arguments:
        game (Game): The game instance for which the map is generated.
        coordinates (tuple[float, float]): The latitude and longitude of the center of the map.
        map_size (int): The size of the map in pixels.
        map_rotated_size (int): The size of the map in pixels after rotation.
        rotation (int): The rotation angle of the map.
        map_directory (str): The directory where the map files are stored.
        logger (Any, optional): The logger to use. Must have at least three basic methods: debug,
            info, warning. If not provided, default logging will be used.
    """

    _map_i3d_path: str | None = None

    def preprocess(self) -> None:
        """Gets the path to the map I3D file from the game instance and saves it to the instance
        attribute. If the game does not support I3D files, the attribute is set to None."""
        try:
            self._map_i3d_path = self.game.i3d_file_path(self.map_directory)
            self.logger.debug("Map I3D path: %s.", self._map_i3d_path)
        except NotImplementedError:
            self.logger.warning("I3D file processing is not implemented for this game.")
            self._map_i3d_path = None

    def process(self) -> None:
        """Updates the map I3D file with the default settings."""
        self._update_i3d_file()
        self._add_fields()
        if self.game.code == "FS25":
            self._add_forests()
            self._add_splines()

    def _get_tree(self) -> ET.ElementTree | None:
        """Returns the ElementTree instance of the map I3D file."""
        if not self._map_i3d_path:
            self.logger.debug("I3D is not obtained, skipping the update.")
            return None
        if not os.path.isfile(self._map_i3d_path):
            self.logger.warning("I3D file not found: %s.", self._map_i3d_path)
            return None

        return ET.parse(self._map_i3d_path)

    def _update_i3d_file(self) -> None:
        """Updates the map I3D file with the default settings."""

        tree = self._get_tree()
        if tree is None:
            return

        self.logger.debug("Map I3D file loaded from: %s.", self._map_i3d_path)

        root = tree.getroot()
        for map_elem in root.iter("Scene"):
            for terrain_elem in map_elem.iter("TerrainTransformGroup"):
                if self.map.shared_settings.change_height_scale:
                    suggested_height_scale = self.map.shared_settings.height_scale_value
                    if suggested_height_scale is not None and suggested_height_scale > 255:
                        new_height_scale = int(
                            self.map.shared_settings.height_scale_value  # type: ignore
                        )
                        terrain_elem.set("heightScale", str(new_height_scale))
                        self.logger.info(
                            "heightScale attribute set to %s in TerrainTransformGroup element.",
                            new_height_scale,
                        )

                self.logger.debug("TerrainTransformGroup element updated in I3D file.")
            sun_elem = map_elem.find(".//Light[@name='sun']")

            if sun_elem is not None:
                self.logger.debug("Sun element found in I3D file.")

                distance = self.map_size // 2

                sun_elem.set("lastShadowMapSplitBboxMin", f"-{distance},-128,-{distance}")
                sun_elem.set("lastShadowMapSplitBboxMax", f"{distance},148,{distance}")

                self.logger.debug(
                    "Sun BBOX updated with half of the map size: %s.",
                    distance,
                )

        if self.map_size > 4096:
            displacement_layer = terrain_elem.find(".//DisplacementLayer")  # pylint: disable=W0631

            if displacement_layer is not None:
                displacement_layer.set("size", str(DISPLACEMENT_LAYER_SIZE_FOR_BIG_MAPS))
                self.logger.debug(
                    "Map size is greater than 4096, DisplacementLayer size set to %s.",
                )

        tree.write(self._map_i3d_path)  # type: ignore
        self.logger.debug("Map I3D file saved to: %s.", self._map_i3d_path)

    def previews(self) -> list[str]:
        """Returns a list of paths to the preview images (empty list).
        The component does not generate any preview images so it returns an empty list.

        Returns:
            list[str]: An empty list.
        """
        return []

    # pylint: disable=R0914, R0915
    def _add_splines(self) -> None:
        """Adds splines to the map I3D file."""
        splines_i3d_path = os.path.join(self.map_directory, "map", "splines.i3d")
        if not os.path.isfile(splines_i3d_path):
            self.logger.warning("Splines I3D file not found: %s.", splines_i3d_path)
            return

        tree = ET.parse(splines_i3d_path)

        textures_info_layer_path = self.get_infolayer_path("textures")
        if not textures_info_layer_path:
            return

        with open(textures_info_layer_path, "r", encoding="utf-8") as textures_info_layer_file:
            textures_info_layer = json.load(textures_info_layer_file)

        roads_polylines: list[list[tuple[int, int]]] | None = textures_info_layer.get(
            "roads_polylines"
        )

        if not roads_polylines:
            self.logger.warning("Roads polylines data not found in textures info layer.")
            return

        self.logger.debug("Found %s roads polylines in textures info layer.", len(roads_polylines))
        self.logger.debug("Starging to add roads polylines to the I3D file.")

        root = tree.getroot()
        # Find <Shapes> element in the I3D file.
        shapes_node = root.find(".//Shapes")
        # Find <Scene> element in the I3D file.
        scene_node = root.find(".//Scene")

        # Read the not resized DEM to obtain Z values for spline points.
        background_component = self.map.get_component("Background")
        if not background_component:
            self.logger.warning("Background component not found.")
            return

        # pylint: disable=no-member
        not_resized_dem = cv2.imread(
            background_component.not_resized_path, cv2.IMREAD_UNCHANGED  # type: ignore
        )
        self.logger.debug(
            "Not resized DEM loaded from: %s. Shape: %s.",
            background_component.not_resized_path,  # type: ignore
            not_resized_dem.shape,
        )
        dem_x_size, dem_y_size = not_resized_dem.shape

        if shapes_node is not None and scene_node is not None:
            node_id = SPLINES_NODE_ID_STARTING_VALUE
            user_attributes_node = root.find(".//UserAttributes")
            if user_attributes_node is None:
                self.logger.warning("UserAttributes node not found in I3D file.")
                return

            for road_id, road in enumerate(roads_polylines, start=1):
                # Add to scene node
                # <Shape name="spline01_CSV" translation="0 0 0" nodeId="11" shapeId="11"/>

                try:
                    fitted_road = self.fit_object_into_bounds(
                        linestring_points=road, angle=self.rotation
                    )
                except ValueError as e:
                    self.logger.warning(
                        "Road %s could not be fitted into the map bounds with error: %s",
                        road_id,
                        e,
                    )
                    continue

                self.logger.debug("Road %s has %s points.", road_id, len(fitted_road))
                fitted_road = self.interpolate_points(
                    fitted_road, num_points=self.map.spline_settings.spline_density
                )
                self.logger.debug(
                    "Road %s has %s points after interpolation.", road_id, len(fitted_road)
                )

                spline_name = f"spline{road_id}"

                shape_node = ET.Element("Shape")
                shape_node.set("name", spline_name)
                shape_node.set("translation", "0 0 0")
                shape_node.set("nodeId", str(node_id))
                shape_node.set("shapeId", str(node_id))

                scene_node.append(shape_node)

                road_ccs = [self.top_left_coordinates_to_center(point) for point in fitted_road]

                # Add to shapes node
                # <NurbsCurve name="spline01_CSV" shapeId="11" degree="3" form="open">

                nurbs_curve_node = ET.Element("NurbsCurve")
                nurbs_curve_node.set("name", spline_name)
                nurbs_curve_node.set("shapeId", str(node_id))
                nurbs_curve_node.set("degree", "3")
                nurbs_curve_node.set("form", "open")

                # Now for each point in the road add the following entry to nurbs_curve_node
                # <cv c="-224.548401, 427.297546, -2047.570312" />
                # The second coordinate (Z) will be 0 at the moment.

                for point_ccs, point in zip(road_ccs, fitted_road):
                    cx, cy = point_ccs
                    x, y = point

                    x = int(x)
                    y = int(y)

                    x = max(0, min(x, dem_x_size - 1))
                    y = max(0, min(y, dem_y_size - 1))

                    z = not_resized_dem[y, x]
                    z /= 32  # Yes, it's a magic number here.

                    cv_node = ET.Element("cv")
                    cv_node.set("c", f"{cx}, {z}, {cy}")

                    nurbs_curve_node.append(cv_node)

                shapes_node.append(nurbs_curve_node)

                # Add UserAttributes to the shape node.
                # <UserAttribute nodeId="5000">
                # <Attribute name="maxSpeedScale" type="integer" value="1"/>
                # <Attribute name="speedLimit" type="integer" value="100"/>
                # </UserAttribute>

                user_attribute_node = ET.Element("UserAttribute")
                user_attribute_node.set("nodeId", str(node_id))

                attributes = [
                    ("maxSpeedScale", "integer", "1"),
                    ("speedLimit", "integer", "100"),
                ]

                for name, attr_type, value in attributes:
                    user_attribute_node.append(I3d.create_attribute_node(name, attr_type, value))

                user_attributes_node.append(user_attribute_node)  # type: ignore

                node_id += 1

            tree.write(splines_i3d_path)  # type: ignore
            self.logger.debug("Splines I3D file saved to: %s.", splines_i3d_path)

    # pylint: disable=R0914, R0915
    def _add_fields(self) -> None:
        """Adds fields to the map I3D file."""
        tree = self._get_tree()
        if tree is None:
            return

        textures_info_layer_path = self.get_infolayer_path("textures")
        if not textures_info_layer_path:
            return

        with open(textures_info_layer_path, "r", encoding="utf-8") as textures_info_layer_file:
            textures_info_layer = json.load(textures_info_layer_file)

        fields: list[list[tuple[int, int]]] | None = textures_info_layer.get("fields")
        if not fields:
            self.logger.warning("Fields data not found in textures info layer.")
            return

        self.logger.debug("Found %s fields in textures info layer.", len(fields))
        self.logger.debug("Starging to add fields to the I3D file.")

        root = tree.getroot()
        gameplay_node = root.find(".//TransformGroup[@name='gameplay']")
        if gameplay_node is not None:
            fields_node = gameplay_node.find(".//TransformGroup[@name='fields']")
            user_attributes_node = root.find(".//UserAttributes")

            if fields_node is not None:
                node_id = NODE_ID_STARTING_VALUE

                # Not using enumerate because in case of the error, we do not increment
                # the field_id. So as a result we do not have a gap in the field IDs.
                field_id = 1

                for field in fields:
                    try:
                        fitted_field = self.fit_object_into_bounds(
                            polygon_points=field, angle=self.rotation
                        )
                    except ValueError as e:
                        self.logger.warning(
                            "Field %s could not be fitted into the map bounds with error: %s",
                            field_id,
                            e,
                        )
                        continue

                    field_ccs = [
                        self.top_left_coordinates_to_center(point) for point in fitted_field
                    ]

                    try:
                        cx, cy = self.get_polygon_center(field_ccs)
                    except Exception as e:  # pylint: disable=W0718
                        self.logger.warning(
                            "Field %s could not be fitted into the map bounds.", field_id
                        )
                        self.logger.debug("Error: %s", e)
                        continue

                    # Creating the main field node.
                    field_node = ET.Element("TransformGroup")
                    field_node.set("name", f"field{field_id}")
                    field_node.set("translation", f"{cx} 0 {cy}")
                    field_node.set("nodeId", str(node_id))

                    # Adding UserAttributes to the field node.
                    user_attribute_node = self.create_user_attribute_node(node_id)
                    user_attributes_node.append(user_attribute_node)  # type: ignore

                    node_id += 1

                    # Creating the polygon points node, which contains the points of the field.
                    polygon_points_node = ET.Element("TransformGroup")
                    polygon_points_node.set("name", "polygonPoints")
                    polygon_points_node.set("nodeId", str(node_id))
                    node_id += 1

                    for point_id, point in enumerate(field_ccs, start=1):
                        rx, ry = self.absolute_to_relative(point, (cx, cy))

                        node_id += 1
                        point_node = ET.Element("TransformGroup")
                        point_node.set("name", f"point{point_id}")
                        point_node.set("translation", f"{rx} 0 {ry}")
                        point_node.set("nodeId", str(node_id))

                        polygon_points_node.append(point_node)

                    field_node.append(polygon_points_node)

                    # Adding the name indicator node to the field node.
                    name_indicator_node, node_id = self.get_name_indicator_node(node_id, field_id)
                    field_node.append(name_indicator_node)

                    # Adding the teleport indicator node to the field node.
                    teleport_indicator_node, node_id = self.get_teleport_indicator_node(node_id)
                    field_node.append(teleport_indicator_node)

                    # Adding the field node to the fields node.
                    fields_node.append(field_node)
                    self.logger.debug("Field %s added to the I3D file.", field_id)

                    node_id += 1
                    field_id += 1

            tree.write(self._map_i3d_path)  # type: ignore
            self.logger.debug("Map I3D file saved to: %s.", self._map_i3d_path)

    def get_name_indicator_node(self, node_id: int, field_id: int) -> tuple[ET.Element, int]:
        """Creates a name indicator node with given node ID and field ID.

        Arguments:
            node_id (int): The node ID of the name indicator node.
            field_id (int): The ID of the field.

        Returns:
            tuple[ET.Element, int]: The name indicator node and the updated node ID.
        """
        node_id += 1
        name_indicator_node = ET.Element("TransformGroup")
        name_indicator_node.set("name", "nameIndicator")
        name_indicator_node.set("nodeId", str(node_id))

        node_id += 1
        note_node = ET.Element("Note")
        note_node.set("name", "Note")
        note_node.set("nodeId", str(node_id))
        note_node.set("text", f"field{field_id}&#xA;0.00 ha")
        note_node.set("color", "4278190080")
        note_node.set("fixedSize", "true")

        name_indicator_node.append(note_node)

        return name_indicator_node, node_id

    def get_teleport_indicator_node(self, node_id: int) -> tuple[ET.Element, int]:
        """Creates a teleport indicator node with given node ID.

        Arguments:
            node_id (int): The node ID of the teleport indicator node.

        Returns:
            tuple[ET.Element, int]: The teleport indicator node and the updated node ID.
        """
        node_id += 1
        teleport_indicator_node = ET.Element("TransformGroup")
        teleport_indicator_node.set("name", "teleportIndicator")
        teleport_indicator_node.set("nodeId", str(node_id))

        return teleport_indicator_node, node_id

    @staticmethod
    def create_user_attribute_node(node_id: int) -> ET.Element:
        """Creates an XML user attribute node with given node ID.

        Arguments:
            node_id (int): The node ID of the user attribute node.

        Returns:
            ET.Element: The created user attribute node.
        """
        user_attribute_node = ET.Element("UserAttribute")
        user_attribute_node.set("nodeId", str(node_id))

        attributes = [
            ("angle", "integer", "0"),
            ("missionAllowed", "boolean", "true"),
            ("missionOnlyGrass", "boolean", "false"),
            ("nameIndicatorIndex", "string", "1"),
            ("polygonIndex", "string", "0"),
            ("teleportIndicatorIndex", "string", "2"),
        ]

        for name, attr_type, value in attributes:
            user_attribute_node.append(I3d.create_attribute_node(name, attr_type, value))

        return user_attribute_node

    @staticmethod
    def create_attribute_node(name: str, attr_type: str, value: str) -> ET.Element:
        """Creates an XML attribute node with given name, type, and value.

        Arguments:
            name (str): The name of the attribute.
            attr_type (str): The type of the attribute.
            value (str): The value of the attribute.

        Returns:
            ET.Element: The created attribute node.
        """
        attribute_node = ET.Element("Attribute")
        attribute_node.set("name", name)
        attribute_node.set("type", attr_type)
        attribute_node.set("value", value)
        return attribute_node

    # pylint: disable=R0911
    def _add_forests(self) -> None:
        """Adds forests to the map I3D file."""
        custom_schema = self.kwargs.get("tree_custom_schema")
        if custom_schema:
            tree_schema = custom_schema
        else:
            try:
                tree_schema_path = self.game.tree_schema
            except ValueError:
                self.logger.warning("Tree schema path not set for the Game %s.", self.game.code)
                return

            if not os.path.isfile(tree_schema_path):
                self.logger.warning("Tree schema file was not found: %s.", tree_schema_path)
                return

            try:
                with open(tree_schema_path, "r", encoding="utf-8") as tree_schema_file:
                    tree_schema = json.load(tree_schema_file)  # type: ignore
            except json.JSONDecodeError as e:
                self.logger.warning(
                    "Could not load tree schema from %s with error: %s", tree_schema_path, e
                )
                return

        texture_component: Texture | None = self.map.get_component("Texture")  # type: ignore
        if not texture_component:
            self.logger.warning("Texture component not found.")
            return

        forest_layer = texture_component.get_layer_by_usage("forest")

        if not forest_layer:
            self.logger.warning("Forest layer not found.")
            return

        weights_directory = self.game.weights_dir_path(self.map_directory)
        forest_image_path = forest_layer.get_preview_or_path(weights_directory)

        if not forest_image_path or not os.path.isfile(forest_image_path):
            self.logger.warning("Forest image not found.")
            return

        tree = self._get_tree()
        if tree is None:
            return

        # Find the <Scene> element in the I3D file.
        root = tree.getroot()
        scene_node = root.find(".//Scene")
        if scene_node is None:
            self.logger.warning("Scene element not found in I3D file.")
            return

        self.logger.debug("Scene element found in I3D file, starting to add forests.")

        node_id = TREE_NODE_ID_STARTING_VALUE

        # Create <TransformGroup name="trees" translation="0 400 0" nodeId="{node_id}"> element.
        trees_node = ET.Element("TransformGroup")
        trees_node.set("name", "trees")
        trees_node.set("translation", "0 400 0")
        trees_node.set("nodeId", str(node_id))
        node_id += 1

        # pylint: disable=no-member
        forest_image = cv2.imread(forest_image_path, cv2.IMREAD_UNCHANGED)

        tree_count = 0
        for x, y in self.non_empty_pixels(forest_image, step=self.map.i3d_settings.forest_density):
            xcs, ycs = self.top_left_coordinates_to_center((x, y))
            node_id += 1

            rotation = randint(-180, 180)
            xcs, ycs = self.randomize_coordinates(  # type: ignore
                (xcs, ycs), self.map.i3d_settings.forest_density
            )

            random_tree = choice(tree_schema)  # type: ignore
            tree_name = random_tree["name"]
            tree_id = random_tree["reference_id"]

            reference_node = ET.Element("ReferenceNode")
            reference_node.set("name", tree_name)  # type: ignore
            reference_node.set("translation", f"{xcs} 0 {ycs}")
            reference_node.set("rotation", f"0 {rotation} 0")
            reference_node.set("referenceId", str(tree_id))
            reference_node.set("nodeId", str(node_id))

            trees_node.append(reference_node)
            tree_count += 1

        scene_node.append(trees_node)
        self.logger.info("Added %s trees to the I3D file.", tree_count)

        tree.write(self._map_i3d_path)  # type: ignore
        self.logger.debug("Map I3D file saved to: %s.", self._map_i3d_path)

    @staticmethod
    def randomize_coordinates(coordinates: tuple[int, int], density: int) -> tuple[float, float]:
        """Randomizes the coordinates of the point with the given density.

        Arguments:
            coordinates (tuple[int, int]): The coordinates of the point.
            density (int): The density of the randomization.

        Returns:
            tuple[float, float]: The randomized coordinates of the point.
        """
        MAXIMUM_RELATIVE_SHIFT = 0.2  # pylint: disable=C0103
        shift_range = density * MAXIMUM_RELATIVE_SHIFT

        x_shift = uniform(-shift_range, shift_range)
        y_shift = uniform(-shift_range, shift_range)

        x, y = coordinates
        x += x_shift  # type: ignore
        y += y_shift  # type: ignore

        return x, y

    @staticmethod
    def non_empty_pixels(
        image: np.ndarray, step: int = 1
    ) -> Generator[tuple[int, int], None, None]:
        """Receives numpy array, which represents single-channeled image of uint8 type.
        Yield coordinates of non-empty pixels (pixels with value greater than 0).

        Arguments:
            image (np.ndarray): The image to get non-empty pixels from.
            step (int, optional): The step to iterate through the image. Defaults to 1.

        Yields:
            tuple[int, int]: The coordinates of non-empty pixels.
        """
        for y, row in enumerate(image[::step]):
            for x, value in enumerate(row[::step]):
                if value > 0:
                    yield x * step, y * step
