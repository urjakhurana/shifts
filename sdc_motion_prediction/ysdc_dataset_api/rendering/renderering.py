import cv2
import numpy as np

from ysdc_dataset_api.utils import (
    get_track_polygon,
    get_transformed_velocity,
    get_transformed_acceleration,
    transform2dpoints,
)


MAX_HISTORY_LENGTH = 25


def _create_feature_map(rows, cols, num_channels):
    shape = [num_channels, rows, cols]
    return np.zeros(shape, dtype=np.float32)


class FeatureMapRendererBase:
    def __init__(
            self,
            config,
            feature_map_params,
            time_grid_params,
            to_feature_map_tf,
    ):
        self._config = config
        self._feature_map_params = feature_map_params
        self._history_indices = self._get_history_indices(time_grid_params)
        self._num_channels = self._get_num_channels()
        self._to_feature_map_tf = to_feature_map_tf

    def render(self, scene, to_track_transform):
        raise NotImplementedError()

    def _get_num_channels(self):
        raise NotImplementedError()

    def _get_history_indices(self, time_grid_params):
        return list(range(
            -time_grid_params['stop'] - 1,
            -time_grid_params['start'],
            time_grid_params['step'],
        ))

    @property
    def n_history_steps(self):
        return len(self._history_indices)

    @property
    def num_channels(self):
        return self._num_channels

    def _create_feature_map(self):
        return _create_feature_map(
            self._feature_map_params['rows'],
            self._feature_map_params['cols'],
            self._num_channels * self.n_history_steps,
        )

    def _get_transformed_track_polygon(self, track, transform):
        polygon = transform2dpoints(get_track_polygon(track), transform)
        polygon = np.around(polygon.reshape(1, -1, 2) - 0.5).astype(np.int32)
        return polygon


class VehicleTracksRenderer(FeatureMapRendererBase):
    def _get_num_channels(self):
        num_channels = 0
        if 'occupancy' in self._config:
            num_channels += 1
        if 'velocity' in self._config:
            num_channels += 2
        if 'acceleration' in self._config:
            num_channels += 2
        if 'yaw' in self._config:
            num_channels += 1
        return num_channels

    def render(self, scene, to_track_transform):
        feature_map = self._create_feature_map()
        transform = self._to_feature_map_tf @ to_track_transform
        for ts_ind in self._history_indices:
            for track in scene.past_vehicle_tracks[ts_ind].tracks:
                track_polygon = self._get_transformed_track_polygon(
                    track, transform)
                for i, v in enumerate(self._get_fm_values(track, to_track_transform)):
                    cv2.fillPoly(
                        feature_map[ts_ind * self.num_channels + i, :, :],
                        track_polygon,
                        v,
                        lineType=cv2.LINE_AA,
                    )
            ego_track = scene.past_ego_track[ts_ind]
            ego_track_polygon = self._get_transformed_track_polygon(ego_track, transform)
            for i, v in enumerate(self._get_fm_values(ego_track, to_track_transform)):
                cv2.fillPoly(
                    feature_map[ts_ind * self.num_channels + i, :, :],
                    ego_track_polygon,
                    v,
                    lineType=cv2.LINE_AA,
                )
        return feature_map

    def _get_fm_values(self, track, to_track_transform):
        values = []
        if 'occupancy' in self._config:
            values.append(1.)
        if 'velocity' in self._config:
            velocity_transformed = get_transformed_velocity(track, to_track_transform)
            values.append(velocity_transformed[0])
            values.append(velocity_transformed[1])
        if 'acceleration' in self._config:
            acceleration_transformed = get_transformed_acceleration(
                track, to_track_transform)
            values.append(acceleration_transformed[0])
            values.append(acceleration_transformed[1])
        if 'yaw' in self._config:
            values.append(track.yaw)
        return values


class PedestrianTracksRenderer(FeatureMapRendererBase):
    def _get_num_channels(self):
        num_channels = 0
        if 'occupancy' in self._config:
            num_channels += 1
        if 'velocity' in self._config:
            num_channels += 2
        return num_channels

    def render(self, scene, to_track_transform):
        feature_map = self._create_feature_map()
        for ts_ind in self._history_indices:
            for track in scene.past_pedestrian_tracks[ts_ind].tracks:
                transform = self._to_feature_map_tf @ to_track_transform
                track_polygon = self._get_transformed_track_polygon(track, transform)
                for i, v in enumerate(self._get_fm_values(track, to_track_transform)):
                    cv2.fillPoly(
                        feature_map[ts_ind * self.num_channels + i, :, :],
                        track_polygon,
                        v,
                        lineType=cv2.LINE_AA,
                    )
        return feature_map

    def _get_fm_values(self, track, to_track_transform):
        values = []
        if 'occupancy' in self._config:
            values.append(1.)
        if 'velocity' in self._config:
            velocity_transformed = get_transformed_velocity(track, to_track_transform)
            values.append(velocity_transformed[0])
            values.append(velocity_transformed[1])
        return values


class FeatureRenderer:
    def __init__(self, config):
        self._feature_map_params = config['feature_map_params']
        self._to_feature_map_tf = self._get_to_feature_map_transform()

        self._renderers = self._create_renderers_list(config)
        self._num_channels = self._get_num_channels()

    def render_features(self, scene, to_track_frame_tf):
        fm = self._create_feature_map()
        slice_start = 0
        for renderer in self._renderers:
            slice_end = slice_start + renderer.num_channels * renderer.n_history_steps
            fm[slice_start:slice_end, :, :] = renderer.render(scene, to_track_frame_tf)
            slice_start = slice_end
        return fm

    @property
    def to_feature_map_tf(self):
        return self._to_feature_map_tf

    def _get_to_feature_map_transform(self):
        fm_scale = 1. / self._feature_map_params['resolution']
        fm_origin_x = 0.5 * self._feature_map_params['rows']
        fm_origin_y = 0.5 * self._feature_map_params['cols']
        return np.array([
            [fm_scale, 0, 0, fm_origin_x],
            [0, fm_scale, 0, fm_origin_y],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ])

    def _create_feature_map(self):
        return _create_feature_map(
            self._feature_map_params['rows'],
            self._feature_map_params['cols'],
            self._num_channels,
        )

    def _get_num_channels(self):
        return sum(
            renderer.num_channels * renderer.n_history_steps
            for renderer in self._renderers
        )

    def _create_renderers_list(self, config):
        renderers = []
        for group in config['renderers_groups']:
            for renderer_config in group['renderers']:
                time_grid_params = self._validate_time_grid(group['time_grid_params'])
                renderers.append(
                    self._create_renderer(
                        renderer_config,
                        self._feature_map_params,
                        time_grid_params,
                        self._to_feature_map_tf,
                    )
                )
        return renderers

    def _create_renderer(
            self,
            config,
            feature_map_params,
            n_history_steps,
            to_feature_map_tf,
    ):
        if 'vehicles' in config:
            return VehicleTracksRenderer(
                config['vehicles'],
                feature_map_params,
                n_history_steps,
                to_feature_map_tf,
            )
        elif 'pedestrians' in config:
            return PedestrianTracksRenderer(
                config['pedestrians'],
                feature_map_params,
                n_history_steps,
                to_feature_map_tf
            )
        else:
            raise NotImplementedError()

    @staticmethod
    def _validate_time_grid(time_grid_params):
        if time_grid_params['start'] < 0:
            raise ValueError('"start" value must be non-negative.')
        if time_grid_params['stop'] < 0:
            raise ValueError('"stop" value must be non-negative')
        if time_grid_params['start'] > time_grid_params['stop']:
            raise ValueError('"start" must be less or equal to "stop"')
        if time_grid_params['stop'] + 1 > MAX_HISTORY_LENGTH:
            raise ValueError(
                'Maximum history size is 25. Consider setting "stop" to 24 or less')
        return time_grid_params
