"""GraphQL documents for the Matterport Model API.

Taken from the public documentation (field and argument names as documented there):
  Sample queries   https://developers.matterport.com/api/model/samples/        (getSweeps, getDimensions, getTags, addTag)
  GeoCoordinates   https://developers.matterport.com/api/model/geocoordinates/ (getGeoCoordinates, geoLocationOf, modelLocationOf)
  Schema reference https://api.matterport.com/model/docs/reference
  Console          https://api.matterport.com/model/graphiql   (paste a query here first when something fails)
Not exercised without credentials. If a field is rejected, check it in the console: the schema is versioned.
"""

MODEL_DETAILS = """
query getModelDetails($modelId: ID!) {
  model(id: $modelId) { id name description publication { address } }
}"""

SWEEPS = """
query getSweeps($modelId: ID!) {
  model(id: $modelId) {
    locations { id position { x, y, z } floor { id } room { id } }
  }
}"""

DIMENSIONS = """
query getDimensions($modelId: ID!) {
  model(id: $modelId) {
    floors { id label dimensions(units: metric) { areaFloor height width depth units } }
    rooms { id label tags dimensions(units: metric) { areaFloor height width depth units } }
  }
}"""

TAGS = """
query getTags($modelId: ID!) {
  model(id: $modelId) {
    mattertags(includeDisabled: false) { id floor { id } enabled color label description position { x, y, z } anchorPosition { x, y, z } }
  }
}"""

GEOCOORDINATES = """
query getGeoCoordinates($modelId: ID!) {
  model(id: $modelId) {
    geocoordinates { source altitude latitude longitude translation { x y z } rotation { x y z w } }
  }
}"""

# model point -> latitude/longitude, computed by Matterport (use it to verify our own georeference)
GEO_LOCATION_OF = """
query getGeoCoordinatesOf($modelId: ID!, $point: IPoint3D!) {
  model(id: $modelId) { geoLocationOf(modelLocation: $point) { lat long } }
}"""

ADD_TAG = """
mutation addTag($modelId: ID!, $floorId: ID!, $color: String, $description: String, $label: String, $enabled: Boolean!,
                $anchorPositionX: Float!, $anchorPositionY: Float!, $anchorPositionZ: Float!,
                $stemEnabled: Boolean!, $stemNormalX: Float!, $stemNormalY: Float!, $stemNormalZ: Float!, $stemLength: Float!) {
  addMattertag(modelId: $modelId, field: id, mattertag: {
    floorId: $floorId, enabled: $enabled, color: $color, label: $label, description: $description,
    anchorPosition: { x: $anchorPositionX, y: $anchorPositionY, z: $anchorPositionZ },
    stemEnabled: $stemEnabled, stemNormal: { x: $stemNormalX, y: $stemNormalY, z: $stemNormalZ }, stemLength: $stemLength
  }) { id }
}"""
