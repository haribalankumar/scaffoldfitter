"""
Fit step for gross alignment and scale.
"""

import copy
from opencmiss.utils.zinc.field import assignFieldParameters, create_field_euler_angles_rotation_matrix
from opencmiss.utils.zinc.finiteelement import getNodeNameCentres
from opencmiss.utils.zinc.general import ChangeManager
from opencmiss.zinc.field import Field
from opencmiss.zinc.optimisation import Optimisation
from opencmiss.zinc.result import RESULT_OK, RESULT_WARNING_PART_DONE
from scaffoldfitter.fitterstep import FitterStep


def createFieldsTransformations(coordinates: Field, rotation_angles=None, scale_value=1.0, \
    translation_offsets=None, translation_scale_factor=1.0):
    """
    Create constant fields for rotation, scale and translation containing the supplied
    values, plus the transformed coordinates applying them in the supplied order.
    :param coordinates: The coordinate field to scale, 3 components.
    :param rotation_angles: List of euler angles, length = number of components.
     See create_field_euler_angles_rotation_matrix.
    :param scale_value: Scalar to multiply all components of coordinates.
    :param translation_offsets: List of offsets, length = number of components.
    :param translation_scale_factor: Scaling to multiply translation by so it's magnitude can remain
    close to other parameters for rotation (radians) and scale (assumed close to unit).
    :return: 4 fields: transformedCoordinates, rotation, scale, translation
    """
    if rotation_angles is None:
        rotation_angles = [0.0, 0.0, 0.0]
    if translation_offsets is None:
        translation_offsets = [0.0, 0.0, 0.0]
    components_count = coordinates.getNumberOfComponents()
    assert (components_count == 3) and (len(rotation_angles) == components_count) and isinstance(scale_value, float) \
        and (len(translation_offsets) == components_count), "createFieldsTransformations.  Invalid arguments"
    fieldmodule = coordinates.getFieldmodule()
    with ChangeManager(fieldmodule):
        # scale, translate and rotate model, in that order
        rotation = fieldmodule.createFieldConstant(rotation_angles)
        scale = fieldmodule.createFieldConstant(scale_value)
        translation = fieldmodule.createFieldConstant(translation_offsets)
        rotation_matrix = create_field_euler_angles_rotation_matrix(fieldmodule, rotation)
        rotated_coordinates = fieldmodule.createFieldMatrixMultiply(components_count, rotation_matrix, coordinates)
        transformed_coordinates = rotated_coordinates*scale + (translation if (translation_scale_factor == 1.0) else \
            translation*fieldmodule.createFieldConstant([ translation_scale_factor ]*components_count))
        assert transformed_coordinates.isValid()
    return transformed_coordinates, rotation, scale, translation

class FitterStepAlign(FitterStep):

    _jsonTypeId = "_FitterStepAlign"

    def __init__(self):
        super(FitterStepAlign, self).__init__()
        self._alignMarkers = False
        self._rotation = [ 0.0, 0.0, 0.0 ]
        self._scale = 1.0
        self._translation = [ 0.0, 0.0, 0.0 ]

    @classmethod
    def getJsonTypeId(cls):
        return cls._jsonTypeId

    def decodeSettingsJSONDict(self, dct : dict):
        """
        Decode definition of step from JSON dict.
        """
        assert self._jsonTypeId in dct
        self._alignMarkers = dct["alignMarkers"]
        self._rotation = dct["rotation"]
        self._scale = dct["scale"]
        self._translation = dct["translation"]

    def encodeSettingsJSONDict(self) -> dict:
        """
        Encode definition of step in dict.
        :return: Settings in a dict ready for passing to json.dump.
        """
        return {
            self._jsonTypeId : True,
            "alignMarkers" : self._alignMarkers,
            "rotation" : self._rotation,
            "scale" : self._scale,
            "translation" : self._translation
            }

    def isAlignMarkers(self):
        return self._alignMarkers

    def setAlignMarkers(self, alignMarkers):
        """
        :param alignMarkers: True to automatically align to markers, otherwise False.
        :return: True if state changed, otherwise False.
        """
        if alignMarkers != self._alignMarkers:
            self._alignMarkers = alignMarkers
            return True
        return False

    def getRotation(self):
        return self._rotation

    def setRotation(self, rotation):
        """
        :param rotation: List of 3 euler angles in radians, order applied:
        0 = azimuth (about z)
        1 = elevation (about rotated y)
        2 = roll (about rotated x)
        :return: True if state changed, otherwise False.
        """
        assert len(rotation) == 3, "FitterStepAlign:  Invalid rotation"
        if rotation != self._rotation:
            self._rotation = copy.copy(rotation)
            return True
        return False

    def getScale(self):
        return self._scale

    def setScale(self, scale):
        """
        :param scale: Real scale.
        :return: True if state changed, otherwise False.
        """
        if scale != self._scale:
            self._scale = scale
            return True
        return False

    def getTranslation(self):
        return self._translation

    def setTranslation(self, translation):
        """
        :param translation: [ x, y, z ].
        :return: True if state changed, otherwise False.
        """
        assert len(translation) == 3, "FitterStepAlign:  Invalid translation"
        if translation != self._translation:
            self._translation = copy.copy(translation)
            return True
        return False

    def run(self):
        """
        Perform align and scale.
        """
        modelCoordinates = self._fitter.getModelCoordinatesField()
        assert modelCoordinates, "Align:  Missing model coordinates"
        if self._alignMarkers:
            self._doAlignMarkers()
        fieldmodule = self._fitter._fieldmodule
        with ChangeManager(fieldmodule):
            # rotate, scale and translate model
            modelCoordinatesTransformed = createFieldsTransformations(
                modelCoordinates, self._rotation, self._scale, self._translation)[0]
            fieldassignment = self._fitter._modelCoordinatesField.createFieldassignment(modelCoordinatesTransformed)
            result = fieldassignment.assign()
            assert result in [ RESULT_OK, RESULT_WARNING_PART_DONE ], "Align:  Failed to transform model"
            self._fitter.updateModelReferenceCoordinates()
            del fieldassignment
            del modelCoordinatesTransformed
        self._fitter.calculateDataProjections(self)
        self.setHasRun(True)

    def _doAlignMarkers(self):
        """
        Prepare and invoke alignment to markers.
        """
        fieldmodule = self._fitter._fieldmodule
        markerGroup = self._fitter.getMarkerGroup()
        assert markerGroup, "Align:  No marker group to align with"
        markerPrefix = markerGroup.getName()
        modelCoordinates = self._fitter.getModelCoordinatesField()
        componentsCount = modelCoordinates.getNumberOfComponents()

        markerNodeGroup, markerLocation, markerCoordinates, markerName = self._fitter.getMarkerModelFields()
        assert markerNodeGroup and markerCoordinates and markerName, "Align:  No marker group, coordinates or name fields"
        modelMarkers = getNodeNameCentres(markerNodeGroup, markerCoordinates, markerName)

        markerDataGroup, markerDataCoordinates, markerDataName = self._fitter.getMarkerDataFields()
        assert markerDataGroup and markerDataCoordinates and markerDataName, "Align:  No marker data group, coordinates or name fields"
        dataMarkers = getNodeNameCentres(markerDataGroup, markerDataCoordinates, markerDataName)

        # match model and data markers, warn of unmatched markers
        markerMap = {}
        writeDiagnostics = self.getDiagnosticLevel() > 0
        for modelName in modelMarkers:
            # name match allows case and whitespace differences
            matchName = modelName.strip().casefold()
            for dataName in dataMarkers:
                if dataName.strip().casefold() == matchName:
                    markerMap[modelName] = ( modelMarkers[modelName], dataMarkers[dataName] )
                    if writeDiagnostics:
                        print("Align:  Model marker '" + modelName + "' found in data" + (" as '" + dataName +"'" if (dataName != modelName) else ""))
                        dataMarkers.pop(dataName)
                    break
            else:
                if writeDiagnostics:
                    print("Align:  Model marker '" + modelName + "' not found in data")
        if writeDiagnostics:
            for dataName in dataMarkers:
                print("Align:  Data marker '" + dataName + "' not found in model")

        self._optimiseAlignment(markerMap)

    def _optimiseAlignment(self, markerMap):
        """
        Calculate transformation from modelCoordinates to dataMarkers
        over the markers, by scaling, translating and rotating model.
        On success, sets transformation parameters in object.
        :param markerMap: dict name -> (modelCoordinates, dataCoordinates)
        """
        assert len(markerMap) >= 3, "Align:  Only " + str(len(markerMap)) + " markers - need at least 3"
        region = self._fitter._context.createRegion()
        fieldmodule = region.getFieldmodule()
        dataScale = self._fitter.getDataScale()
        with ChangeManager(fieldmodule):
            modelCoordinates = fieldmodule.createFieldFiniteElement(3)
            dataCoordinates = fieldmodule.createFieldFiniteElement(3)
            nodes = fieldmodule.findNodesetByFieldDomainType(Field.DOMAIN_TYPE_NODES)
            nodetemplate = nodes.createNodetemplate()
            nodetemplate.defineField(modelCoordinates)
            nodetemplate.defineField(dataCoordinates)
            fieldcache = fieldmodule.createFieldcache()
            for name, positions in markerMap.items():
                modelx = positions[0]
                datax = positions[1]
                node = nodes.createNode(-1, nodetemplate)
                fieldcache.setNode(node)
                result1 = modelCoordinates.assignReal(fieldcache, positions[0])
                result2 = dataCoordinates.assignReal(fieldcache, positions[1])
                assert (result1 == RESULT_OK) and (result2 == RESULT_OK), "Align:  Failed to set up data for alignment to markers optimisation"
            del fieldcache
            modelCoordinatesTransformed, rotation, scale, translation = createFieldsTransformations(modelCoordinates, translation_scale_factor=dataScale)
            # create objective = sum of squares of vector from modelCoordinatesTransformed to dataCoordinates
            markerDiff = fieldmodule.createFieldSubtract(dataCoordinates, modelCoordinatesTransformed)
            scaledMarkerDiff = markerDiff*fieldmodule.createFieldConstant([ 1.0/dataScale ]*3)
            objective = fieldmodule.createFieldNodesetSumSquares(scaledMarkerDiff, nodes)
            #objective = fieldmodule.createFieldNodesetSum(fieldmodule.createFieldMagnitude(scaledMarkerDiff), nodes)
            assert objective.isValid(), "Align:  Failed to set up objective function for alignment to markers optimisation"

        # future: pre-fit to avoid gimbal lock

        optimisation = fieldmodule.createOptimisation()
        optimisation.setMethod(Optimisation.METHOD_LEAST_SQUARES_QUASI_NEWTON)
        #optimisation.setMethod(Optimisation.METHOD_QUASI_NEWTON)
        optimisation.addObjectiveField(objective)
        optimisation.addIndependentField(rotation)
        optimisation.addIndependentField(scale)
        optimisation.addIndependentField(translation)

        #FunctionTolerance = optimisation.getAttributeReal(Optimisation.ATTRIBUTE_FUNCTION_TOLERANCE)
        #GradientTolerance = optimisation.getAttributeReal(Optimisation.ATTRIBUTE_GRADIENT_TOLERANCE)
        #StepTolerance = optimisation.getAttributeReal(Optimisation.ATTRIBUTE_STEP_TOLERANCE)
        #MaximumStep = optimisation.getAttributeReal(Optimisation.ATTRIBUTE_MAXIMUM_STEP)
        #MinimumStep = optimisation.getAttributeReal(Optimisation.ATTRIBUTE_MINIMUM_STEP)
        #LinesearchTolerance = optimisation.getAttributeReal(Optimisation.ATTRIBUTE_LINESEARCH_TOLERANCE)
        #TrustRegionSize = optimisation.getAttributeReal(Optimisation.ATTRIBUTE_TRUST_REGION_SIZE)

        #tol_scale = dataScale*dataScale
        #FunctionTolerance *= tol_scale
        #optimisation.setAttributeReal(Optimisation.ATTRIBUTE_FUNCTION_TOLERANCE, FunctionTolerance)
        #GradientTolerance *= tol_scale
        #optimisation.setAttributeReal(Optimisation.ATTRIBUTE_GRADIENT_TOLERANCE, GradientTolerance)
        #StepTolerance *= tol_scale
        #optimisation.setAttributeReal(Optimisation.ATTRIBUTE_STEP_TOLERANCE, StepTolerance)
        #MaximumStep *= tol_scale
        #optimisation.setAttributeReal(Optimisation.ATTRIBUTE_MAXIMUM_STEP, MaximumStep)
        #MinimumStep *= tol_scale
        #optimisation.setAttributeReal(Optimisation.ATTRIBUTE_MINIMUM_STEP, MinimumStep)
        #LinesearchTolerance *= dataScale
        #optimisation.setAttributeReal(Optimisation.ATTRIBUTE_LINESEARCH_TOLERANCE, LinesearchTolerance)
        #TrustRegionSize *= dataScale
        #optimisation.setAttributeReal(Optimisation.ATTRIBUTE_TRUST_REGION_SIZE, TrustRegionSize)

        #if self.getDiagnosticLevel() > 0:
        #    print("Function Tolerance", FunctionTolerance)
        #    print("Gradient Tolerance", GradientTolerance)
        #    print("Step Tolerance", StepTolerance)
        #    print("Maximum Step", MaximumStep)
        #    print("Minimum Step", MinimumStep)
        #    print("Linesearch Tolerance", LinesearchTolerance)
        #    print("Trust Region Size", TrustRegionSize)

        result = optimisation.optimise()
        if self.getDiagnosticLevel() > 1:
            solutionReport = optimisation.getSolutionReport()
            print(solutionReport)
        assert result == RESULT_OK, "Align:  Alignment to markers optimisation failed"

        fieldcache = fieldmodule.createFieldcache()
        result1, self._rotation = rotation.evaluateReal(fieldcache, 3)
        result2, self._scale = scale.evaluateReal(fieldcache, 1)
        result3, self._translation = translation.evaluateReal(fieldcache, 3)
        self._translation = [ s*dataScale for s in self._translation ]
        assert (result1 == RESULT_OK) and (result2 == RESULT_OK) and (result3 == RESULT_OK), "Align:  Failed to evaluate transformation for alignment to markers"
