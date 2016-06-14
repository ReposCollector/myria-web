var margin_pi = {top_pi: 10, right_pi: 200, bottom_pi: 30, left_pi: 50},
    width_pi = 400 - margin_pi.left_pi - margin_pi.right_pi,
    height_pi = 180 - margin_pi.top_pi - margin_pi.bottom_pi;

var x_pi = d3.scale.linear()
        .domain([0,50])
        .range([0,250])
        .clamp(true);

var y_pi = d3.scale.linear()
    .range([height_pi, 0]);

var xAxis_pi = d3.svg.axis()
    .scale(x_pi)
    .orient("bottom")
    .tickFormat(d3.format('d'));

var yAxis_pi = d3.svg.axis()
    .scale(y_pi)
    .orient("left").ticks(5)
    .tickFormat(d3.format('d'));

var currentError_pi = d3.svg.line()
    .x(function(d) { return x(d.queryID); })
    .y(function(d) { return y(d.PIControlProportionalErrorValue); });

var errorSum_pi = d3.svg.line()
    .x(function(d) { return x(d.queryID); }) 
    .y(function(d) { return y(d.PIControlIntegralErrorSum); });

var svg_pi = d3.select("#piError").append("svg")
    .attr("width", width_pi + margin_pi.left_pi + margin_pi.right_pi)
    .attr("height", height_pi + margin_pi.top_pi + margin_pi.bottom_pi)
  .append("g")
    .attr("transform", "translate(" + margin_pi.left_pi + "," + margin_pi.top_pi + ")");

var currentErrorPath = null;
var errorSumPath = null;

var userPoints_pi  = []

var firstObj_pi = {}
firstObj_pi.queryID = "0"
firstObj_pi.PIControlProportionalErrorValue = "0"
firstObj_pi.PIControlIntegralErrorSum = "0"
userPoints_pi.push(firstObj_pi)

x_pi.domain(d3.extent(userPoints_pi, function(d) { return d.queryID; }));

svg_pi.append("g")
      .attr("class", "x axis")
      .attr("transform", "translate(0," + height_pi + ")")
      .call(xAxis_pi)
    .append("text")
      .attr("y", 28)
      .attr("x", 115)
      .text("Query ID");

y_pi.domain(d3.extent(userPoints_pi, function(d) { return d.PIControlIntegralErrorSum; }));

svg_pi.append("g")
    .attr("class", "y axis")
    .call(yAxis_pi)
  .append("text")
    .attr("transform", "rotate(-90)")
    .attr("y", 0- 30)
    .attr("x",0 - (height_pi / 2) )
    .style("text-anchor", "middle")
    .text("Error");

currentErrorPath = svg_pi.append("path")
    .attr("class", "lineCurrentError")
    .attr("d", currentError_pi(userPoints_pi));

errorSumPath =  svg_pi.append("path")
    .attr("class", "lineErrorSum")
    .attr("d", errorSum_pi(userPoints_pi))

function updatePIErrorLines() {

      var newDataPoint_pi = {}
      newDataPoint_pi.queryID = ithQuery

      $.when(getRequest('/perfenforce/scaling-algorithm-state')).done(function(scalingState){

        newDataPoint_pi.PIControlProportionalErrorValue = scalingState.PIControlProportionalErrorValue
        newDataPoint_pi.PIControlIntegralErrorSum = scalingState.PIControlIntegralErrorSum

        console.log(newDataPoint_pi)

        userPoints_pi.push(newDataPoint_pi)

        x_pi.domain(d3.extent(userPoints_pi, function(d) { return d.queryID; }))
        svg_pi.select("g.x.axis") // change the x axis
            .transition(2000)
            .call(xAxis_pi);

        y_pi.domain(d3.extent(userPoints_pi, function(d) { return d.PIControlIntegralErrorSum; }));
        svg_pi.select("g.y.axis") // change the x axis
            .transition(2000)
            .call(yAxis_pi);

        svg_pi.select(".lineCurrentError")   // change the line
            .transition(2000)
            .attr("d", currentError_pi(userPoints_pi));

        svg_pi.select(".lineErrorSum")   // change the line
            .transition(2000)
            .attr("d", errorSum_pi(userPoints_pi));

      });
}
