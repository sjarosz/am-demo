<!--
  Copyright 2018-2025 Ping Identity Corporation. All Rights Reserved

 ! This code is to be used exclusively in connection with Ping Identity
 ! Corporation software or services. Ping Identity Corporation only offers
 ! such software or services to legal entities who have entered into a
 ! binding license agreement with Ping Identity Corporation.
-->

# Setting Up the ForgeRock Access Management Sample Dashboard

The ForgeRock Access Management sample dashboard is a Grafana dashboard that graphs data stored in Prometheus:

- By default, AM servers publish Prometheus-format metrics at `/json/metrics/prometheus.`
- Prometheus regularly pulls metrics from each AM server.
- Grafana graphs metrics queried from Prometheus.

_**Disclaimer:**
This sample is provided on an "as is" basis, without warranty of any kind, to
the fullest extent permitted by law. ForgeRock does not warrant or guarantee
the individual success developers may have in implementing the code on their
development platforms or in production configurations. ForgeRock does not
warrant, guarantee or make any representations regarding the use, results
of use, accuracy, timeliness or completeness of any data or information
relating to this sample. ForgeRock disclaims all warranties, expressed or
implied, and in particular, disclaims all warranties of merchantability, and
warranties related to the code, or any service or software related thereto.
ForgeRock shall not be liable for any direct, indirect or consequential
damages or costs of any type arising out of any action taken by you or others
related to the sample._

To prepare to use the dashboard, set up and configure the following:

## 1 - Install ForgeRock Access Management

Please see AM Documentation for guidance on this step. The rest of this README assumes this is complete.


## 2 - Enable the Global Monitoring Service
As per the AM documentation, enable the Monitoring Service. This can be found under
Configure -> Global Services -> Monitoring. The switch is called 'Monitoring Status'.


## 3 - Enable the Prometheus Endpoint
As per the AM documentation, enable the Prometheus Endpoint. This can be found under
Configure -> Global Services -> Monitoring -> Secondary Configurations -> Prometheus Reporter.


## 4 - Install Prometheus
Install a Prometheus instance. Please see the Prometheus 'Getting Started' guide for further details on this.
[Prometheus Getting Started Guide](https://prometheus.io/docs/prometheus/latest/getting_started/)


## 5 - Install Grafana
Install a Grafana instance. Please see the Grafana installation guide and the 'Getting Started' guide for further details on this.

[Grafana Installation Guide](http://docs.grafana.org/installation/)

[Grafana Getting Started Guide](http://docs.grafana.org/guides/getting_started/)



#### The following sections assume you have installed and running instances of AM, Prometheus and Grafana.


## 6 - Configure Prometheus to scrape data from ForgeRock Access Management

To allow Prometheus to scrape data from AM, the prometheus.yml configuration must be updated with the location of the
AM server(s), as well as the correct credentials. Note the credentials listed in the prometheus.yml file are sent in
each request from Prometheus to AM, and MUST match the credentials found in the AM Prometheus Reporter configuration.

After updating this configuration, you will need to restart Prometheus. You can check that Prometheus is successfully
scraping from AM by going to `http://localhost:9090/targets` and confirming that the state for each of your defined targets is UP.

Below is a sample prometheus.yml file, set up to scrape from two AM instances. Note the global scrape interval is
set to 15 seconds. 

Note: When using Docker on MacOS, you will need to reference localhost using the 'docker.for.mac.localhost' alias.

```
global:
    scrape_interval: 15s
    scrape_timeout: 15s
    evaluation_interval: 1m

scrape_configs:

  - job_name: 'am1'
    metrics_path: '/openam/json/metrics/prometheus'
    basic_auth:
        username: "prometheus"
        password: "prometheus"
    static_configs:
      - targets: ['localhost:8080']

  - job_name: 'am2'
    metrics_path: '/openam/json/metrics/prometheus'
    basic_auth:
        username: "prometheus"
        password: "prometheus"
    static_configs:
      - targets: ['localhost:9080']
```

## 7 - Configure Grafana Data Source to use Prometheus

Before Grafana can query Prometheus for data, you must configure a Data Source in Grafana. This tells Grafana
where Prometheus data is available. You can find more details on this setup at the following link:
[Grafana Datasource Guice](http://docs.grafana.org/features/datasources/prometheus/)

To use the sample dashboards provided here, the Data Source MUST be called "PROMETHEUS" in uppercase. If you used the
default settings, your Data Source configuration should look like this:

```
Name:     PROMETHEUS
Type:     Prometheus
Default:  (checked)

URL:      http://localhost:9090
Access:   Direct
```

## 8 - Import the sample AM dashboard

To import the sample dashboard into Grafana, follow the steps outlined in the below Grafana documentation:
[Grafana Import Guide](http://docs.grafana.org/reference/export_import/)

To extend or edit the sample dashboard, you may wish to reference the ForgeRock Access Management Documentation, which
contains further details on the available metrics.


## 9 - Troubleshooting

#### I see no data in my Grafana graphs

Check in Prometheus (Step 6) that Prometheus is successfully scraping
data from at least one AM instance. You can use the 'Graph' tooling built-in to the Prometheus web UI to
confirm that data is available. 

#### Prometheus is failing to scrape data from AM

Check that Prometheus is configured to send the 'Basic Auth' header with each request. Confirm that the
username and password configured in prometheus.yml match those defined in the Prometheus Reporter
in the AM configuration. Check that both the Monitoring Service and the Prometheus Endpoint are enabled
in the AM configuration. 

#### The sample graphs will not import into Grafana

Ensure you have configured a Data Source in Grafana as per step 7 above. It must be called
'PROMETHEUS' in uppercase. Confirm in the Data Source menu in Grafana that this Data Source is created
and can succesfully connect to Prometheus (use the 'Save and Test' button to test this).

Ensure you are running version 5 of Grafana - the dashboard samples were created using V5 of Grafana 
and there is limited backwards compatibility. 



The sample dashboards are suitable for demonstration purposes only.
