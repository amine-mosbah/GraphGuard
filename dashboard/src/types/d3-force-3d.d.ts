/**
 * Minimal typings for `d3-force-3d`, which ships none.
 *
 * Only the surface the network map uses is declared. A hand-written stub is preferable to
 * `any` on the import: the shape below is what the layout code relies on, so a future
 * upgrade that changes it becomes a type error rather than a graph that silently renders
 * every node at the origin.
 */
declare module "d3-force-3d" {
  export interface SimulationNode {
    id: string;
    x: number;
    y: number;
    z: number;
    [key: string]: unknown;
  }

  export interface Simulation<N> {
    force(name: string, force: unknown): Simulation<N>;
    tick(iterations?: number): Simulation<N>;
    stop(): Simulation<N>;
    nodes(): N[];
  }

  export interface LinkForce {
    id(accessor: (node: { id: string }) => string): LinkForce;
    distance(value: number): LinkForce;
    strength(value: number): LinkForce;
  }

  export interface ManyBodyForce {
    strength(value: number): ManyBodyForce;
  }

  /** `numDimensions` of 3 is what makes this the 3D variant rather than plain d3-force. */
  export function forceSimulation<N>(nodes: N[], numDimensions?: number): Simulation<N>;
  export function forceLink(links: { source: string; target: string }[]): LinkForce;
  export function forceManyBody(): ManyBodyForce;
  export function forceCenter(x?: number, y?: number, z?: number): unknown;
}
