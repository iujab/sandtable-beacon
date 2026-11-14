"""GraphQL documents for the Matterport Model API, checked against the live schema.

Endpoint https://api.matterport.com/api/models/graph, HTTP basic auth (API token id : secret).
Every field below was confirmed by introspection (`__type`) and by a successful query with the
event's credentials. What changed against this morning's documentation-only version:

  - `model.publication.address` is a free-text string; the structured address is `model.address`
    (an `Address` object: `address`, `locality`, `administrativeArea`, `postalCode`, ...), and the
    model's own point is `model.geolocation { lat long }` (strings).
  - Rooms are named by `label` (often empty), `tags` and `classifications { label }` ("Office",
    "Hallway", "Staircase", "Bathroom", "Void", ...). Room geometry is real: `boundary { edges { id } }`
    references the floor's `edges`, whose `vertices` give 2-D positions in the model frame (metres).
  - Walls are floor `edges` of `type: wall`; doors are `openings` on an edge (`type`: doorway,
    opening, window, divider; `relativeCenter` is a 0..1 fraction along the edge, `width` in metres).
  - Sweeps (`locations`) carry Matterport's own `neighbors` (the navigation graph) and a room id.
  - `geocoordinates` exists (source "processing" on the models seen); its `geoLocationOf(modelLocation)`
    sub-field converts any model point to latitude/longitude, which is how the georeference is fitted.
  - `addMattertag(modelId, field: id, mattertag: MattertagDetails!)` matches the schema; the adapter
    only ever runs it in dry-run mode unless MATTERPORT_WRITE_TAGS=1.

Schema reference https://api.matterport.com/model/docs/reference ; console https://api.matterport.com/model/graphiql
"""

SEARCH = """
query searchModels($query: String!, $pageSize: Int, $offset: String) {
  models(query: $query, pageSize: $pageSize, offset: $offset) {
    totalResults nextOffset
    results {
      id name state visibility
      address { address locality administrativeArea postalCode status }
      geolocation { lat long }
      floors { count }
      rooms { count labels }
      mattertags { count }
      assets { locations { count } }
    }
  }
}"""

# One request for everything the twin needs (the API answers it in about a second for a 100-sweep model).
MODEL = """
query getModelForTwin($modelId: ID!) {
  model(id: $modelId) {
    id name state visibility created
    address { address locality administrativeArea postalCode status }
    geolocation { lat long }
    geocoordinates { source latitude longitude altitude rotation { x y z w } translation { x y z } }
    dimensions(units: metric) { areaFloor width depth height }
    floors {
      id label sequence
      dimensions(units: metric) { areaFloor width depth height }
      vertices { id position { x y } }
      edges { id type thickness vertices { id } openings { id type label width relativeCenter } }
    }
    rooms {
      id label tags keywords
      classifications { label }
      floor { id }
      dimensions(units: metric) { areaFloor width depth height }
      boundary { edges { id } }
      holes { edges { id } }
    }
    locations { id label floor { id } room { id } position { x y z } neighbors tags }
    mattertags(includeDisabled: false) { id label description color enabled floor { id } anchorPosition { x y z } }
  }
}"""

# Floor-plan assets: metadata only (the URLs are short-lived signed links; nothing is downloaded by default).
FLOORPLANS = """
query getFloorplans($modelId: ID!) {
  model(id: $modelId) {
    assets { floorplans { id format provider flags width height resolution origin { x y } floor { id } status } }
  }
}"""

# Model point -> latitude/longitude, computed by Matterport (used to fit and to verify the georeference).
GEO_LOCATION_OF = """
query geoLocationOf($modelId: ID!, $point: IPoint3D!) {
  model(id: $modelId) { geocoordinates { geoLocationOf(modelLocation: $point) { lat long status } } }
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

# Fields of the MattertagDetails input the ADD_TAG document uses (checked against introspection in dry-run mode).
ADD_TAG_INPUT_FIELDS = ("floorId", "enabled", "color", "label", "description", "anchorPosition", "stemEnabled", "stemNormal", "stemLength")

INTROSPECT_TYPE = """
query introspectType($name: String!) {
  __type(name: $name) { name kind fields { name } inputFields { name } enumValues { name } }
}"""
