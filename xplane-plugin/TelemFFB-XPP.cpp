#include <stdio.h>
#include <string.h>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <cstdarg>
#include <string>
#include <map>
#include <cstring>
#include <winsock2.h>
#include "XPLMProcessing.h"
#include "XPLMDataAccess.h"
#include "XPLMUtilities.h"
#include "XPLMPlugin.h"
#include "XPLMPlanes.h"



/* UDP socket variables */
SOCKET udpSocket;
struct sockaddr_in serverAddr;

/* Data refs we will record. */
static XPLMDataRef gPaused = XPLMFindDataRef("sim/time/paused");
static XPLMDataRef gGs_axil = XPLMFindDataRef("sim/flightmodel/forces/g_axil");
static XPLMDataRef gGs_nrml = XPLMFindDataRef("sim/flightmodel/forces/g_nrml"); // "G's"
static XPLMDataRef gGs_side = XPLMFindDataRef("sim/flightmodel/forces/g_side");
static XPLMDataRef gAccLocal_x = XPLMFindDataRef("sim/flightmodel/position/local_ax");
static XPLMDataRef gAccLocal_y = XPLMFindDataRef("sim/flightmodel/position/local_ay");
static XPLMDataRef gAccLocal_z = XPLMFindDataRef("sim/flightmodel/position/local_az");
static XPLMDataRef gTAS = XPLMFindDataRef("sim/flightmodel/position/true_airspeed");
static XPLMDataRef gAirDensity = XPLMFindDataRef("sim/weather/rho");
static XPLMDataRef gAoA = XPLMFindDataRef("sim/flightmodel/position/alpha");
static XPLMDataRef gWoW = XPLMFindDataRef("sim/flightmodel2/gear/tire_vertical_deflection_mtr");
static XPLMDataRef gPlaneLat = XPLMFindDataRef("sim/flightmodel/position/latitude");
static XPLMDataRef gPlaneLon = XPLMFindDataRef("sim/flightmodel/position/longitude");
static XPLMDataRef gPlaneEl = XPLMFindDataRef("sim/flightmodel/position/elevation");

std::map<std::string, std::string> telemetryData;

static float MyFlightLoopCallback(float inElapsedSinceLastCall, float inElapsedTimeSinceLastFlightLoop, int inCounter, void* inRefcon);

const float kt_2_mps = 0.514444; // convert knots to meters per second

std::string FloatToString(float value, int precision)
{
    std::ostringstream stream;
    stream << std::fixed << std::setprecision(precision) << value;
    return stream.str();
}

void CollectTelemetryData()
{
    // Get the aircraft name
    char aircraftName[256];
    char aircraftPath[256];
    XPLMGetNthAircraftModel(0, aircraftName, aircraftPath);

    // Strip the file extension from the aircraft name
    char* lastDot = strrchr(aircraftName, '.');
    if (lastDot != nullptr) {
        *lastDot = '\0';
    }

    // Add the aircraft name to telemetryData
    telemetryData["src"] = "XPLANE";
    telemetryData["N"] = aircraftName;
    telemetryData["STOP"] = std::to_string(XPLMGetDatai(gPaused));
    // Collect relevant data and populate the telemetryData map
    telemetryData["T"] = FloatToString(XPLMGetElapsedTime(), 3);
    telemetryData["G"] = FloatToString(XPLMGetDataf(gGs_nrml), 3);
    telemetryData["Gz"] = FloatToString(XPLMGetDataf(gGs_axil), 3);
    telemetryData["Gx"] = FloatToString(XPLMGetDataf(gGs_side), 3);

    telemetryData["TAS"] = FloatToString(XPLMGetDataf(gTAS), 3);
    telemetryData["AirDensity"] = FloatToString(XPLMGetDataf(gAirDensity), 3);
    telemetryData["AoA"] = FloatToString(XPLMGetDataf(gAoA), 3);

    int numGear;
    numGear = XPLMGetDatavf(gWoW, NULL, 0, 0);
    telemetryData["NumGear"] = std::to_string(numGear);

    float wow_array[10];
    // Retrieve the entire array of values
    XPLMGetDatavf(gWoW, wow_array, 0, 9);

    std::ostringstream wow;

    // Set precision for floating-point values
    wow << std::fixed << std::setprecision(3);

    for (int i = 0; i < 5; ++i) {
        wow << wow_array[i];
        if (i < 4) {
            wow << "~";  // Add tilde separator between values, except for the last one
        }
    }

    std::string wowString = wow.str();

    telemetryData["WeightOnWheels"] = wow.str();

    telemetryData["AccBody"] =  FloatToString(XPLMGetDataf(gAccLocal_x), 3) + "~" + FloatToString(XPLMGetDataf(gAccLocal_y), 3) + "~" + FloatToString(XPLMGetDataf(gAccLocal_z), 3);

    telemetryData["Latitude"] = FloatToString(XPLMGetDataf(gPlaneLat), 3);
    telemetryData["Longitude"] = FloatToString(XPLMGetDataf(gPlaneLon), 3);
    telemetryData["Elevation"] = FloatToString(XPLMGetDataf(gPlaneEl), 3);


}



void FormatAndSendTelemetryData()
{
    // Create a string with the data for UDP transmission
    std::string dataString;

    for (const auto& entry : telemetryData) {
        dataString += entry.first + "=" + entry.second + ";";
    }

    // Send the data over the UDP socket
    sendto(udpSocket, dataString.c_str(), dataString.length(), 0, (struct sockaddr*)&serverAddr, sizeof(serverAddr));
}



PLUGIN_API int XPluginStart(char* outName, char* outSig, char* outDesc)
{

    strcpy(outName, "TelemFFB-XPP");
    strcpy(outSig, "vpforce.telemffb.xpplugin");
    strcpy(outDesc, "Collect and send Telemetry for FFB processing");


    /* Find the data refs we want to record. */



    // Initialize Winsock
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
    {
        XPLMDebugString("Failed to initialize Winsock\n");
        return 0;
    }

    // Create a UDP socket
    udpSocket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);

    if (udpSocket == INVALID_SOCKET)
    {
        XPLMDebugString("Failed to create UDP socket\n");
        WSACleanup();
        return 0;
    }

    // Set up server address information
    memset(&serverAddr, 0, sizeof(serverAddr));
    serverAddr.sin_family = AF_INET;
    serverAddr.sin_port = htons(34390); // Set the desired port number
    serverAddr.sin_addr.s_addr = inet_addr("127.255.255.255"); // Send to localhost (127.0.0.1)

    /* Register our callback for once a second.  Positive intervals
     * are in seconds, negative are the negative of sim frames.  Zero
     * registers but does not schedule a callback for time. */
    XPLMRegisterFlightLoopCallback(
        MyFlightLoopCallback, /* Callback */
        -1,                  /* Interval */
        NULL);                /* refcon not used. */

    return 1;
}

PLUGIN_API void XPluginStop(void)
{
    /* Unregister the callback */
    XPLMUnregisterFlightLoopCallback(MyFlightLoopCallback, NULL);

    // Close the UDP socket
    closesocket(udpSocket);
    WSACleanup();
}

PLUGIN_API void XPluginDisable(void)
{
    /* do any clean up here */
}

PLUGIN_API int XPluginEnable(void)
{
    return 1;
}

PLUGIN_API void XPluginReceiveMessage(XPLMPluginID inFromWho, int inMessage, void* inParam)
{

}

float MyFlightLoopCallback(float inElapsedSinceLastCall, float inElapsedTimeSinceLastFlightLoop, int inCounter, void* inRefcon)
{

    // Collect telemetry data
    CollectTelemetryData();

    // Format and send telemetry data
    FormatAndSendTelemetryData();

    // Return -1 to indicate we want to be called on next opportunity
    return -1;
}
