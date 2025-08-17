from matplotlib import cm
import matplotlib.pyplot as plt
from collections import defaultdict
from plotly.subplots import make_subplots
import plotly.graph_objects as go
import numpy as np
import time

def plot_3d_bar_with_color_based_on_z_avg(X_new, y_truth, file_name='tmp.html', y_truth_stddev=None):
    # Preprocess the data to calculate average z values for the same x and y
    data_dict = {}
    for i, x_entry in enumerate(X_new):
        if (x_entry['cache_in_mb'], x_entry['bw_in_gbps']) not in data_dict:
            data_dict[(x_entry['cache_in_mb'], x_entry['bw_in_gbps'])] = []
        if y_truth[i] > 0.0001:
            data_dict[(x_entry['cache_in_mb'], x_entry['bw_in_gbps'])].append(y_truth[i])

    # Calculate average z values
    X_new_avg = np.array([[x, y] for x, y in data_dict.keys()])
    y_truth_avg = [np.mean(data_dict[key]) for key in data_dict.keys()]
    y_truth_stddev_list = y_truth_stddev.tolist() if isinstance(y_truth_stddev, np.ndarray) else []

    # Create a 3D scatter plot using Plotly with color based on z values
    fig = make_subplots(rows=1, cols=1, specs=[[{'type': 'scatter3d'}]])
    fig.add_trace(
        go.Scatter3d(
            x=X_new_avg[:, 0],
            y=X_new_avg[:, 1],
            z=y_truth_avg,
            mode='markers',
            marker=dict(
                size=10,
                color=y_truth_avg,
                colorscale='Viridis',
                showscale=True
            ),
            error_z=dict(
                type='data',
                array=y_truth_stddev_list,
                visible=True,
                color='red'
            ) if y_truth_stddev_list else {},
            name='Measured'
        )
    )

    # Set labels and title
    fig.update_layout(
        scene=dict(
            xaxis_title='Cache in MB',
            yaxis_title='Bandwidth in GBps',
            zaxis_title='Relative performance (e.g., L3 Miss)'
        ),
        title='Gaussian Process Regressor-based estimation :: ' + file_name
    )

    # Save the plot as an HTML file
    fig.write_html(file_name)

def plot_3d_with_cache_bw_over_time_animation(data_over_time, file_name='tmp.html'):
    frames = []
    steps = []

    for frame_idx, (X_new, y_truth, y_truth_stddev) in enumerate(data_over_time):
        # data formatting
        data_dict = {}
        for i, x_entry in enumerate(X_new):
            if (x_entry['cache_in_mb'], x_entry['bw_in_gbps']) not in data_dict:
                data_dict[(x_entry['cache_in_mb'], x_entry['bw_in_gbps'])] = []
            if y_truth[i] > 0.0001:
                data_dict[(x_entry['cache_in_mb'], x_entry['bw_in_gbps'])].append(y_truth[i])
        # Calculate average z values
        X_new_avg = np.array([[x, y] for x, y in data_dict.keys()])
        y_truth_avg = [np.mean(data_dict[key]) for key in data_dict.keys()]
        y_truth_stddev_list = y_truth_stddev.tolist() if isinstance(y_truth_stddev, np.ndarray) else []

        # Create a Scatter3d trace for this state
        trace = go.Scatter3d(
            x=X_new_avg[:, 0],
            y=X_new_avg[:, 1],
            z=y_truth_avg,
            mode='markers',
            marker=dict(
                size=10,
                color=y_truth_avg,
                colorscale='Viridis',
                showscale=True
            ),
            error_z=dict(
                type='data',
                array=y_truth_stddev_list,
                visible=True,
                color='red'
            ) if y_truth_stddev_list else {},
            name='Measured'
        )

        # Add this trace to the frames list
        frames.append(go.Frame(data=[trace], name=str(frame_idx)))

        # Add this step to the steps list
        steps.append(dict(method='animate', args=[[str(frame_idx)]], label=str(frame_idx)))

    # Create a Scatter3d trace for the initial state
    initial_trace = [frames[0]['data'][0]]

    # Create a layout with a slider that controls the animation
    layout = go.Layout(
        scene=dict(
            xaxis_title='Cache in MB',
            yaxis_title='Bandwidth in GBps',
            zaxis_title='Relative performance (e.g., L3 Miss)'
        ),
        title='Gaussian Process Regressor-based estimation :: ' + file_name,
        updatemenus=[dict(
            type='buttons',
            showactive=False,
            buttons=[dict(label='Play', method='animate', args=[None, dict(frame=dict(duration=500, redraw=True), fromcurrent=True, transition=dict(duration=0))])],
        )],
        sliders=[dict(steps=steps)]
    )

    # Create a Figure with the initial trace and the layout
    fig = go.Figure(data=initial_trace, layout=layout, frames=frames)

    # Save the Figure as an HTML file
    fig.write_html(file_name)